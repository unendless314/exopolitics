"""
Publish run orchestrator: reconciliation, deterministic generation planning
and atomic pointer switching.

Rewritten in Phase B1 of
known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md. The whole
run holds a single-writer process lock and one logical run timestamp
(``run_ts``). After reconciliation commits the DB state, the whole generation
phase — plan build, fingerprint pass and write pass — runs inside one held
``BEGIN IMMEDIATE`` SQLite transaction, so every artifact in a generation
comes from exactly one database snapshot and the writer slot is reserved up
front (concurrent upstream writers fail at their own ``BEGIN IMMEDIATE``
rather than silently interleaving). The plan's
``content_fingerprint`` is compared against ``current.json``: a changed
fingerprint (or ``rebuild``, or a missing pointer) builds a complete new
generation; a no-change run only atomically refreshes the pointer's
``last_successful_run_at``. The pre-B1 per-file promotion, filesystem
rollback and DB compensation machinery is gone: readers never see partial
output, the DB may briefly run ahead after a failure, and the next
successful run converges by state comparison.

Facade re-exports (Phase A surviving-code split): validation.py owns payload
validation, slug generation, UI label checks and payload assembly, and
reconciliation.py owns the pure reconciliation diff. Existing callers and
tests reference the validation symbols through this module's namespace, so
the re-exports above must be kept (at least orchestrate_run, ValidationError,
slugify, generate_slug, validate_item_payload and get_disclosure_note).
``get_utc_now_iso8601`` is imported into this namespace on purpose: the
test-suite FakeClock patches it here (and in the database namespace) to pin
``run_ts``.
"""
import logging
import pathlib
from typing import Any, Dict

from . import generation, generation_store
from .config import PublishConfig
from .database import PublishRepository, get_connection, transaction, get_utc_now_iso8601
from .process_lock import ProcessLock
from .reconciliation import compute_reconciliation_diff
from .validation import (
    ValidationError,
    slugify,
    generate_slug,
    validate_item_payload,
    get_disclosure_note,
    assemble_item_payload,
)

logger = logging.getLogger("publish.orchestrator")


async def orchestrate_run(
    config: PublishConfig,
    db_path: pathlib.Path,
    export_dir: pathlib.Path,
    rebuild: bool = False
) -> Dict[str, Any]:
    """
    Orchestrate an incremental run or a full rebuild.

    Both modes reconcile the DB with upstream state; whether a new export
    generation is built is decided by state comparison (rebuild always
    builds). Returns the run summary dict (status, published_count,
    withdrawn_count, errors).
    """
    # The entire run (staging, DB state, pointer switch) is serialized
    # through a single-writer process lock.
    lock = ProcessLock(pathlib.Path(db_path).parent / "publish_runner.lock")
    lock.acquire()
    try:
        # One logical run timestamp for the whole run: reconciliation writes,
        # archive stamping, stats, generation id and pointer fields all use
        # exactly this value.
        run_ts = get_utc_now_iso8601()
        conn = get_connection(db_path)
        try:
            repo = PublishRepository(conn)

            # 1. Target Language Existence Validation (Section 7.1)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='translation_output'")
            if not cursor.fetchone():
                raise RuntimeError("Database tables do not exist yet. Run migrate first.")

            cursor.execute("SELECT DISTINCT language_code FROM translation_output WHERE translation_status = 'completed'")
            completed_languages = {row[0] for row in cursor.fetchall()}

            emitted_warnings = set()
            for lang in config.target_languages:
                if lang not in completed_languages:
                    if lang not in emitted_warnings:
                        logger.warning(f"Target language '{lang}' has zero completed translations in the database.")
                        emitted_warnings.add(lang)

            # 2. Reconciliation Candidate Selection & Diff
            candidates = repo.get_reconciliation_candidates()
            active_statuses = repo.get_active_publish_statuses()
            reconciliation_diff = compute_reconciliation_diff(
                candidates,
                active_statuses,
                set(config.target_languages.keys()),
            )
            candidates_by_item = reconciliation_diff.candidates_by_item
            items_to_publish_or_update = reconciliation_diff.items_to_publish_or_update
            items_to_withdraw = reconciliation_diff.items_to_withdraw

            # 3. Slug Assignment & DB updates
            existing_slugs = repo.get_all_frozen_slugs()

            published_count = 0
            withdrawn_count = 0

            # --- A. Reconciliation Phase (Database State Sync) ---
            # First, update the database status for publications/updates
            # (with in-memory validation first). No compensation records are
            # kept: the Phase B1 failure model lets the DB run ahead of the
            # live generation and converges via the next successful run's
            # state comparison.
            for item_id, lang, fingerprint in items_to_publish_or_update:
                # 1. Fetch or generate slug in memory first
                pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                if not pub_rec:
                    title_src = ""
                    lang_map = candidates_by_item[item_id]
                    if "en" in lang_map:
                        title_src = lang_map["en"]["display_title"]
                    else:
                        for r in lang_map.values():
                            if r["display_title"]:
                                title_src = r["display_title"]
                                break
                    slug = generate_slug(title_src, existing_slugs)
                    # Keep in existing_slugs to avoid collision within this batch
                    existing_slugs.add(slug)
                else:
                    slug = pub_rec["slug"]

                # 2. Assemble and validate the export payload before mutating database
                payload_row = repo.fetch_canonical_item_payload(item_id, lang)
                if not payload_row:
                    raise ValidationError(f"Canonical data missing for item {item_id} lang {lang}")

                item_payload = assemble_item_payload(dict(payload_row), slug, published_at=None)
                validate_item_payload(item_payload)

                # 3. DB Transaction: Update metadata & status
                with transaction(conn, commit=True):
                    # Re-query inside transaction to ensure concurrency/idempotency safety
                    pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                    if not pub_rec:
                        # We reuse the generated slug from above
                        publish_record_id = repo.insert_publish_record(item_id, slug, run_ts, now=run_ts)
                    else:
                        publish_record_id = pub_rec["publish_record_id"]
                        slug = pub_rec["slug"]
                        repo.update_publish_record_updated_at(publish_record_id, run_ts)

                    repo.upsert_publish_language_status(
                        publish_record_id=publish_record_id,
                        language_code=lang,
                        publish_status='published',
                        published_at=run_ts,
                        withdrawn_at=None,
                        source_fingerprint=fingerprint,
                        now=run_ts,
                    )

                published_count += 1

            # Second, update database status for withdrawals
            for item_id, lang, slug, fingerprint in items_to_withdraw:
                with transaction(conn, commit=True):
                    pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                    if pub_rec:
                        repo.upsert_publish_language_status(
                            publish_record_id=pub_rec["publish_record_id"],
                            language_code=lang,
                            publish_status='withdrawn',
                            published_at=None,
                            withdrawn_at=run_ts,
                            source_fingerprint=fingerprint,  # Preserve previously published fingerprint!
                            now=run_ts,
                        )
                        repo.update_publish_record_updated_at(pub_rec["publish_record_id"], run_ts)

                withdrawn_count += 1

            # Third, drop publish_archive_metadata rows for languages no
            # longer configured (language-set shrink): the next generation
            # simply contains no artifacts for them, so the publish-owned
            # write metadata must not outlive them (DATA_CONTRACT.md section
            # 2.3). Unconditional and without compensation: a config change
            # alters the fingerprint header, so convergence never depends on
            # delete timing.
            for meta_lang in repo.get_archive_metadata_languages():
                if meta_lang in config.target_languages:
                    continue
                for meta_row in repo.get_archive_metadata_for_language(meta_lang):
                    with transaction(conn, commit=True):
                        repo.delete_archive_metadata(meta_lang, meta_row["archive_month"])

            # --- B. Generation Phase ---
            # One explicit SQLite transaction covers the whole generation
            # phase — pointer read, plan build, fingerprint pass and the
            # write pass all see exactly one DB snapshot. It is opened with BEGIN IMMEDIATE so the writer slot
            # (RESERVED lock) is reserved up front: a concurrent upstream
            # writer (curate/translate) fails at its own BEGIN IMMEDIATE
            # instead of starting writes that would doom both sides — a
            # deferred transaction would let it begin, then fail this
            # connection's shared-to-writer lock upgrade at the metadata
            # commit. Readers are unaffected (SHARED is compatible with
            # RESERVED), and the pipeline runs modules sequentially, so this
            # never contends in normal operation.
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Read and validate the live pointer; a corrupt pointer or live
                # generation is fail-stop, never a silent rebuild trigger.
                pointer = generation_store.read_pointer(export_dir)
                current_hashes: Dict[str, str] = {}
                fallback_root = None
                if pointer is not None:
                    live_root = generation_store.generation_root_for(export_dir, pointer["generation"])
                    current_hashes = generation_store.load_current_generation_hashes(live_root)
                    fallback_root = live_root

                # Deterministic generation plan from the stable DB snapshot;
                # the fingerprint covers the planned final state.
                plan, content_fingerprint = generation.build_generation_plan(
                    repo, config, current_hashes, fallback_root, run_ts, rebuild
                )

                build_needed = rebuild
                if not build_needed:
                    if pointer is None:
                        # Bootstrap: the first successful run always builds a
                        # complete (possibly empty) generation.
                        build_needed = True
                    elif pointer["content_fingerprint"] != content_fingerprint:
                        build_needed = True

                if build_needed:
                    generations_dir = export_dir / generation_store.GENERATIONS_DIR_NAME
                    generation_id = generation_store.allocate_generation_id(generations_dir, run_ts)
                    generation_store.write_generation_to_staging(
                        export_dir,
                        generation.iter_planned_artifact_bytes(plan, repo, config),
                        generation=generation_id,
                        created_at=run_ts,
                        content_fingerprint=content_fingerprint,
                        languages=list(config.target_languages.keys()),
                    )
                    if rebuild:
                        # The rebuild summary reports the full active published
                        # set, read from the same snapshot the generation was
                        # built from.
                        published_count = len(repo.get_published_item_rows())
                    # Sync publish_archive_metadata to the same plan (upsert
                    # every planned stamp, delete rows whose month is no longer
                    # active). The writes join the held snapshot transaction;
                    # committing here ends the snapshot, after which the DB may
                    # legitimately run ahead of the live pointer — the next
                    # successful run converges by state comparison.
                    for lang in config.target_languages:
                        planned_months = set()
                        for month in plan.archive_months[lang]:
                            planned_months.add(month)
                            repo.upsert_archive_metadata(
                                lang, month, plan.archive_stamps[(lang, month)], now=run_ts
                            )
                        for meta_row in repo.get_archive_metadata_for_language(lang):
                            if meta_row["archive_month"] not in planned_months:
                                repo.delete_archive_metadata(lang, meta_row["archive_month"])
                    conn.commit()
                    # The pointer switch is the commit point: readers see either
                    # the complete old generation or the complete new one.
                    generation_store.write_pointer_atomic(export_dir, {
                        "generation": generation_id,
                        "export_completed_at": run_ts,
                        "last_successful_run_at": run_ts,
                        "languages": list(config.target_languages.keys()),
                        "content_fingerprint": content_fingerprint,
                    })
                    # Retention only after a successful switch; warn-only.
                    generation_store.sweep_retired_generations(
                        export_dir, protected_generation=generation_id
                    )
                else:
                    # No-change successful run: refresh only the freshness
                    # signal; the generation directory and stats stay untouched.
                    refreshed_pointer = dict(pointer)
                    refreshed_pointer["last_successful_run_at"] = run_ts
                    generation_store.write_pointer_atomic(export_dir, refreshed_pointer)

                # End the snapshot for the paths that wrote nothing to the DB
                # (no-op where the build path already committed above).
                conn.commit()
            except BaseException:
                # Read-only snapshot paths roll back to nothing; on the build
                # path the metadata commit already stands (DB ahead), which the
                # next successful run converges. Either way the live pointer is
                # untouched.
                conn.rollback()
                raise

            return {
                "status": "success",
                "published_count": published_count,
                "withdrawn_count": withdrawn_count,
                "errors": []
            }

        finally:
            # Best-effort staging cleanup (a failed build leaves it behind;
            # a successful one already moved it into generations/).
            generation_store.discard_staging(export_dir)
            # Close the connection this run opened. sqlite3's internal statement
            # cache forms a reference cycle with the connection, so without an
            # explicit close the file handle is released only at the whim of the
            # cyclic GC (on Windows the locked database file then fails test
            # teardown with PermissionError).
            conn.close()
    finally:
        lock.release()
