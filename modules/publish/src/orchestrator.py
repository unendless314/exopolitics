import json
import os
import pathlib
import shutil
import sqlite3
import logging
from typing import Dict, Any, List, Set, Tuple

from .config import PublishConfig
from .database import PublishRepository, get_connection, transaction, get_utc_now_iso8601
from .reconciliation import compute_reconciliation_diff
from .validation import (
    ValidationError,
    slugify,
    generate_slug,
    validate_item_payload,
    get_disclosure_note,
    assemble_item_payload,
)

# Facade re-exports (Phase A surviving-code split,
# known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md):
# validation.py now owns payload validation, slug generation, UI label
# checks and payload assembly, and reconciliation.py owns the pure
# reconciliation diff. Existing callers and tests reference the validation
# symbols through this module's namespace, so the re-exports above must be
# kept (at least orchestrate_run, ValidationError, slugify, generate_slug,
# validate_item_payload and get_disclosure_note).

logger = logging.getLogger("publish.orchestrator")


def _is_symlink_or_reparse_point(path: pathlib.Path) -> bool:
    """True for symlinks and Windows reparse points (junctions etc.).

    ``os.path.islink`` alone misses junctions on Windows (they are reparse
    points, not symlinks), so the lstat reparse tag is checked as well.
    """
    try:
        if os.path.islink(path):
            return True
        return getattr(os.lstat(path), "st_reparse_tag", 0) != 0
    except FileNotFoundError:
        return False

def rollback_db_state(conn: sqlite3.Connection, db_compensations: List[Dict[str, Any]]) -> None:
    repo = PublishRepository(conn)
    # We rollback in reverse order of modifications
    for comp in reversed(db_compensations):
        item_id = comp.get("source_item_id")
        lang = comp["language_code"]

        with transaction(conn, commit=True):
            if comp["type"] == "publish":
                # Rollback publish
                if comp["had_lang_status"]:
                    # Restore previous language status
                    prev = comp["lang_status"]
                    repo.upsert_publish_language_status(
                        publish_record_id=prev["publish_record_id"],
                        language_code=lang,
                        publish_status=prev["publish_status"],
                        published_at=prev["published_at"],
                        withdrawn_at=prev["withdrawn_at"],
                        source_fingerprint=prev["source_fingerprint"]
                    )
                else:
                    # Delete newly created language status row
                    if comp["had_pub_rec"] and comp["pub_rec"]:
                        pub_rec_id = comp["pub_rec"]["publish_record_id"]
                    else:
                        pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                        pub_rec_id = pub_rec["publish_record_id"] if pub_rec else None

                    if pub_rec_id is not None:
                        cursor = conn.cursor()
                        cursor.execute(
                            "DELETE FROM publish_language_status WHERE publish_record_id = ? AND language_code = ?",
                            (pub_rec_id, lang)
                        )

                # Restore publish_record updated_at
                if comp["had_pub_rec"] and comp["pub_rec"]:
                    repo.update_publish_record_updated_at(
                        comp["pub_rec"]["publish_record_id"],
                        comp["pub_rec"]["updated_at"]
                    )
                elif not comp["had_pub_rec"]:
                    # Delete newly created publish record
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM publish_record WHERE source_item_id = ?", (item_id,))

            elif comp["type"] == "withdraw":
                # Rollback withdraw
                if comp["had_lang_status"]:
                    prev = comp["lang_status"]
                    repo.upsert_publish_language_status(
                        publish_record_id=prev["publish_record_id"],
                        language_code=lang,
                        publish_status=prev["publish_status"],
                        published_at=prev["published_at"],
                        withdrawn_at=prev["withdrawn_at"],
                        source_fingerprint=prev["source_fingerprint"]
                    )
                # Restore publish_record updated_at
                if comp["had_pub_rec"] and comp["pub_rec"]:
                    repo.update_publish_record_updated_at(
                        comp["pub_rec"]["publish_record_id"],
                        comp["pub_rec"]["updated_at"]
                    )

            elif comp["type"] == "archive_meta":
                # Rollback an archive metadata change: restore the prior row
                # exactly, or delete the row if it did not exist before.
                month = comp["archive_month"]
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM publish_archive_metadata WHERE language_code = ? AND archive_month = ?",
                    (lang, month)
                )
                if comp["prior"] is not None:
                    prior = comp["prior"]
                    cursor.execute(
                        "INSERT INTO publish_archive_metadata (language_code, archive_month, updated_at, created_at) VALUES (?, ?, ?, ?)",
                        (lang, month, prior["updated_at"], prior["created_at"])
                    )

async def orchestrate_run(
    config: PublishConfig,
    db_path: pathlib.Path,
    export_dir: pathlib.Path,
    rebuild: bool = False
) -> Dict[str, Any]:
    """
    Orchestrate incremental run or full rebuild.
    """
    # Initialize connection
    conn = get_connection(db_path)
    staging_dir = export_dir / ".staging"
    db_compensations = []

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

        # We track which items and languages were mutated in this run
        mutated_pairs: Set[Tuple[int, str]] = set()

        published_count = 0
        withdrawn_count = 0

        # --- A. Reconciliation Phase (Database State Sync) ---
        # First, update the database status for publications/updates (with in-memory validation first)
        for item_id, lang, fingerprint in items_to_publish_or_update:
            # 1. Fetch or generate slug in memory first and capture initial state
            pub_rec = repo.get_publish_record_by_source_item_id(item_id)
            had_pub_rec = pub_rec is not None
            prior_lang_status = None
            if pub_rec:
                prior_lang_status = repo.get_publish_language_status(pub_rec["publish_record_id"], lang)
            had_lang_status = prior_lang_status is not None

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

            # Record prior state for database compensation if something fails later
            db_compensations.append({
                "type": "publish",
                "source_item_id": item_id,
                "language_code": lang,
                "had_pub_rec": had_pub_rec,
                "pub_rec": dict(pub_rec) if pub_rec else None,
                "had_lang_status": had_lang_status,
                "lang_status": dict(prior_lang_status) if prior_lang_status else None
            })

            # 3. DB Transaction: Update metadata & status
            with transaction(conn, commit=True):
                # Re-query inside transaction to ensure concurrency/idempotency safety
                pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                if not pub_rec:
                    # We reuse the generated slug from above
                    first_pub_time = get_utc_now_iso8601()
                    publish_record_id = repo.insert_publish_record(item_id, slug, first_pub_time)
                else:
                    publish_record_id = pub_rec["publish_record_id"]
                    slug = pub_rec["slug"]
                    repo.update_publish_record_updated_at(publish_record_id, get_utc_now_iso8601())

                repo.upsert_publish_language_status(
                    publish_record_id=publish_record_id,
                    language_code=lang,
                    publish_status='published',
                    published_at=get_utc_now_iso8601(),
                    withdrawn_at=None,
                    source_fingerprint=fingerprint
                )

            mutated_pairs.add((item_id, lang))

        # Second, update database status for withdrawals
        for item_id, lang, slug, fingerprint in items_to_withdraw:
            pub_rec = repo.get_publish_record_by_source_item_id(item_id)
            prior_lang_status = None
            if pub_rec:
                prior_lang_status = repo.get_publish_language_status(pub_rec["publish_record_id"], lang)

            db_compensations.append({
                "type": "withdraw",
                "source_item_id": item_id,
                "language_code": lang,
                "had_pub_rec": pub_rec is not None,
                "pub_rec": dict(pub_rec) if pub_rec else None,
                "had_lang_status": prior_lang_status is not None,
                "lang_status": dict(prior_lang_status) if prior_lang_status else None
            })

            with transaction(conn, commit=True):
                pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                if pub_rec:
                    repo.upsert_publish_language_status(
                        publish_record_id=pub_rec["publish_record_id"],
                        language_code=lang,
                        publish_status='withdrawn',
                        published_at=None,
                        withdrawn_at=get_utc_now_iso8601(),
                        source_fingerprint=fingerprint # Preserve previously published fingerprint!
                    )
                    repo.update_publish_record_updated_at(pub_rec["publish_record_id"], get_utc_now_iso8601())

            mutated_pairs.add((item_id, lang))
            withdrawn_count += 1

        # Third, drop publish_archive_metadata rows for languages no longer
        # configured (language-set shrink): their archive artifacts are
        # removed from the export tree during the promotion phase, so the
        # publish-owned write metadata must not outlive them
        # (DATA_CONTRACT.md section 2.3).
        for meta_lang in repo.get_archive_metadata_languages():
            if meta_lang in config.target_languages:
                continue
            for meta_row in repo.get_archive_metadata_for_language(meta_lang):
                db_compensations.append({
                    "type": "archive_meta",
                    "language_code": meta_lang,
                    "archive_month": meta_row["archive_month"],
                    "prior": dict(meta_row)
                })
                with transaction(conn, commit=True):
                    repo.delete_archive_metadata(meta_lang, meta_row["archive_month"])

        # Set up staging directory (clear it first to start clean)
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)

        # --- B. File Emission Phase into Staging ---
        if rebuild:
            # Re-fetch all published status records from DB to rebuild all eligible files
            published_rows = repo.get_published_item_rows()
        else:
            # Incremental run: only write the new/updated items to staging
            # Reconstruct the fields for newly published/updated items
            published_rows = []
            for item_id, lang, fingerprint in items_to_publish_or_update:
                pub_rec = repo.get_publish_record_by_source_item_id(item_id)
                pls_row = repo.get_publish_language_status(pub_rec["publish_record_id"], lang)
                published_rows.append({
                    "source_item_id": item_id,
                    "language_code": lang,
                    "slug": pub_rec["slug"],
                    "published_at": pls_row["published_at"]
                })

        for row in published_rows:
            item_id, lang, slug, published_at = row["source_item_id"], row["language_code"], row["slug"], row["published_at"]

            payload_row = repo.fetch_canonical_item_payload(item_id, lang)
            if not payload_row:
                raise ValidationError(f"Canonical data missing for published item {item_id} lang {lang}")

            item_json = assemble_item_payload(dict(payload_row), slug, published_at)
            validate_item_payload(item_json)

            item_file_dir = staging_dir / lang / "items"
            item_file_dir.mkdir(parents=True, exist_ok=True)
            item_file_path = item_file_dir / f"{slug}.json"

            with open(item_file_path, "w", encoding="utf-8") as f:
                json.dump(item_json, f, indent=2, ensure_ascii=False)
            published_count += 1

        # 4. Rebuilding Aggregate Files into Staging
        # Compute affected months
        affected_months_by_lang: Dict[str, Set[str]] = {lang: set() for lang in config.target_languages}

        if rebuild:
            # Find all months for active published items in DB
            for lang in config.target_languages:
                affected_months_by_lang[lang] = {m for m in repo.get_active_archive_months(lang) if m}
        else:
            # Find months for mutated items. Pairs whose language is no
            # longer configured (language-set shrink) have their item
            # artifacts withdrawn but their aggregates are not managed.
            for item_id, lang in mutated_pairs:
                if lang not in affected_months_by_lang:
                    continue
                published_at = repo.get_source_item_published_at(item_id)
                if published_at:
                    month = published_at[:7]  # YYYY-MM
                    affected_months_by_lang[lang].add(month)

        batch_size = config.execution_policy.batch_size
        latest_limit = config.index_policy.latest_limit

        for lang in config.target_languages:
            # --- 4.1 Rebuild Latest Index.json ---
            index_items = []
            offset = 0
            while len(index_items) < latest_limit:
                # Query in batches to respect Section 9.3 memory scalability
                query_limit = min(batch_size, latest_limit - len(index_items))
                rows = repo.fetch_latest_index_batch(lang, query_limit, offset)
                if not rows:
                    break

                for row in rows:
                    index_items.append({
                        "slug": row["slug"],
                        "display_title": row["display_title"],
                        "summary_short": row["summary_short"],
                        "canonical_url": row["canonical_url"],
                        "source_published_at": row["source_published_at"],
                        "approved_at": row["approved_at"],
                        "published_at": row["published_at"]
                    })

                offset += len(rows)

            lang_dir = staging_dir / lang
            lang_dir.mkdir(parents=True, exist_ok=True)
            index_path = lang_dir / "index.json"
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(index_items, f, indent=2, ensure_ascii=False)

            # --- 4.2a Self-heal missing archive metadata ---
            # An active month without a publish_archive_metadata row predates
            # the table (databases created before v002). Rewrite its archive
            # once in this run so the row records a real file write; the
            # runner never stamps metadata for an archive it did not write
            # (DATA_CONTRACT.md section 2.3). A full rebuild already rewrites
            # every active month, so no healing is needed there.
            if not rebuild:
                for heal_month in repo.get_active_archive_months(lang):
                    if not heal_month or heal_month in affected_months_by_lang[lang]:
                        continue
                    if repo.get_archive_metadata(lang, heal_month) is None:
                        affected_months_by_lang[lang].add(heal_month)

            # --- 4.2 Rebuild Affected Monthly Archives ---
            archives_dir = lang_dir / "archives"
            archives_dir.mkdir(parents=True, exist_ok=True)

            # Logical write timestamps for archives (re)written in this run,
            # recorded in publish_archive_metadata for the manifest contract.
            written_month_timestamps: Dict[str, str] = {}

            for month in affected_months_by_lang[lang]:
                archive_items = []
                offset = 0
                while True:
                    rows = repo.fetch_archive_month_batch(lang, month, batch_size, offset)
                    if not rows:
                        break

                    for row in rows:
                        archive_items.append({
                            "slug": row["slug"],
                            "display_title": row["display_title"],
                            "summary_short": row["summary_short"],
                            "canonical_url": row["canonical_url"],
                            "source_published_at": row["source_published_at"],
                            "approved_at": row["approved_at"],
                            "published_at": row["published_at"]
                        })
                    offset += len(rows)

                month_file_name = f"archive_{month.replace('-', '_')}.json"
                archive_file_path = archives_dir / month_file_name

                if archive_items:
                    write_timestamp = get_utc_now_iso8601()
                    with open(archive_file_path, "w", encoding="utf-8") as f:
                        json.dump(archive_items, f, indent=2, ensure_ascii=False)
                    written_month_timestamps[month] = write_timestamp

            # --- 4.2b Sync publish-owned archive write metadata ---
            # Archives (re)written in this run get this run's logical clock;
            # affected months whose archive became empty lose their row along
            # with the file deletion during promotion; untouched months keep
            # their existing timestamps. A full rebuild also drops metadata
            # for months that are no longer active at all.
            for month in affected_months_by_lang[lang]:
                prior_meta = repo.get_archive_metadata(lang, month)
                if month in written_month_timestamps:
                    db_compensations.append({
                        "type": "archive_meta",
                        "language_code": lang,
                        "archive_month": month,
                        "prior": dict(prior_meta) if prior_meta else None
                    })
                    with transaction(conn, commit=True):
                        repo.upsert_archive_metadata(lang, month, written_month_timestamps[month])
                elif prior_meta is not None:
                    db_compensations.append({
                        "type": "archive_meta",
                        "language_code": lang,
                        "archive_month": month,
                        "prior": dict(prior_meta)
                    })
                    with transaction(conn, commit=True):
                        repo.delete_archive_metadata(lang, month)

            if rebuild:
                for meta_row in repo.get_archive_metadata_for_language(lang):
                    stale_month = meta_row["archive_month"]
                    if stale_month not in affected_months_by_lang[lang]:
                        db_compensations.append({
                            "type": "archive_meta",
                            "language_code": lang,
                            "archive_month": stale_month,
                            "prior": dict(meta_row)
                        })
                        with transaction(conn, commit=True):
                            repo.delete_archive_metadata(lang, stale_month)

            # --- 4.3 Rebuild Archives Manifest ---
            manifest_rows = repo.get_archive_month_item_counts(lang)
            manifest_json = []
            for row in manifest_rows:
                m_month = row["archive_month"]
                if not m_month:
                    continue
                meta_row = repo.get_archive_metadata(lang, m_month)
                if meta_row is None:
                    # The 4.2a heal (incremental) and the full archive
                    # rewrite (rebuild) guarantee a metadata row for every
                    # active month; a missing row here means the archive
                    # writes and the manifest query disagree, which is a
                    # runner bug, not a recoverable state.
                    raise RuntimeError(
                        f"Missing publish_archive_metadata row for active month {m_month} lang {lang}"
                    )
                manifest_json.append({
                    "archive_month": m_month,
                    "file_name": f"archive_{m_month.replace('-', '_')}.json",
                    "item_count": row["item_count"],
                    "updated_at": meta_row["updated_at"]
                })

            manifest_path = archives_dir / "index.json"
            if manifest_json:
                with open(manifest_path, "w", encoding="utf-8") as f:
                    json.dump(manifest_json, f, indent=2, ensure_ascii=False)

        # --- 5. Rebuild Global Stats.json into Staging ---
        stats_json = {}

        # 5.1 total_active_published_items_by_language
        stats_json["total_active_published_items_by_language"] = repo.count_publish_language_statuses("published")
        for lang in config.target_languages:
            if lang not in stats_json["total_active_published_items_by_language"]:
                stats_json["total_active_published_items_by_language"][lang] = 0

        # 5.2 total_withdrawn_items_by_language
        stats_json["total_withdrawn_items_by_language"] = repo.count_publish_language_statuses("withdrawn")
        for lang in config.target_languages:
            if lang not in stats_json["total_withdrawn_items_by_language"]:
                stats_json["total_withdrawn_items_by_language"][lang] = 0

        # 5.3 latest_index_count_by_language
        stats_json["latest_index_count_by_language"] = {}
        for lang in config.target_languages:
            count = stats_json["total_active_published_items_by_language"][lang]
            stats_json["latest_index_count_by_language"][lang] = min(count, latest_limit)

        # 5.4 archive_month_count_by_language
        stats_json["archive_month_count_by_language"] = {}
        # 5.5 oldest_archive_month_by_language
        stats_json["oldest_archive_month_by_language"] = {}
        for lang in config.target_languages:
            month_count, oldest_month = repo.get_archive_month_stats(lang)
            stats_json["archive_month_count_by_language"][lang] = month_count
            stats_json["oldest_archive_month_by_language"][lang] = oldest_month

        stats_json["last_export_run_timestamp"] = get_utc_now_iso8601()

        stats_path = staging_dir / "stats.json"
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats_json, f, indent=2, ensure_ascii=False)

        # --- C. Safe Replace / Promotion Phase with Backup & Restore ---
        export_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = export_dir / ".backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Collect staging relative file paths
        staging_files = set()
        for root, dirs, files in os.walk(staging_dir):
            for file in files:
                src_path = pathlib.Path(root) / file
                rel_path = src_path.relative_to(staging_dir)
                staging_files.add(rel_path)

        promoted_actions = []
        try:
            # Promote staging files (sorted for a deterministic promotion order,
            # which keeps failure-injection tests and real-failure debugging sane)
            for rel_path in sorted(staging_files):
                src_path = staging_dir / rel_path
                dest_path = export_dir / rel_path

                if dest_path.exists():
                    # Backup existing file
                    backup_path = backup_dir / rel_path
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest_path, backup_path)
                    promoted_actions.append({"type": "replace", "rel_path": rel_path, "had_existing": True})
                else:
                    promoted_actions.append({"type": "replace", "rel_path": rel_path, "had_existing": False})

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src_path, dest_path)

            # Clean up / delete obsolete files
            if rebuild:
                # For rebuild, delete any .json file in export_dir that was not in staging
                for lang in config.target_languages:
                    items_dir = export_dir / lang / "items"
                    if items_dir.exists():
                        for p in items_dir.glob("*.json"):
                            rel_p = p.relative_to(export_dir)
                            if rel_p not in staging_files:
                                backup_path = backup_dir / rel_p
                                backup_path.parent.mkdir(parents=True, exist_ok=True)
                                os.replace(p, backup_path)
                                promoted_actions.append({"type": "delete", "rel_path": rel_p})

                    archives_dir = export_dir / lang / "archives"
                    if archives_dir.exists():
                        for p in archives_dir.glob("*.json"):
                            rel_p = p.relative_to(export_dir)
                            if rel_p not in staging_files:
                                backup_path = backup_dir / rel_p
                                backup_path.parent.mkdir(parents=True, exist_ok=True)
                                os.replace(p, backup_path)
                                promoted_actions.append({"type": "delete", "rel_path": rel_p})

                    manifest_path = export_dir / lang / "archives" / "index.json"
                    if manifest_path.exists() and (pathlib.Path(lang) / "archives" / "index.json") not in staging_files:
                        rel_p = manifest_path.relative_to(export_dir)
                        backup_path = backup_dir / rel_p
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(manifest_path, backup_path)
                        promoted_actions.append({"type": "delete", "rel_path": rel_p})
            else:
                # Incremental run:
                # 1. Clean up withdrawn item files
                for item_id, lang, slug, fingerprint in items_to_withdraw:
                    lang_dir = export_dir / lang
                    item_dir = lang_dir / "items"
                    if lang not in config.target_languages and (
                        _is_symlink_or_reparse_point(lang_dir)
                        or _is_symlink_or_reparse_point(item_dir)
                    ):
                        # A removed language's directory that links outside
                        # the export tree, or whose item directory does, is
                        # no longer publish's to clean; never delete through
                        # either link (see the sweep below).
                        logger.warning(
                            f"Skipping cleanup of withdrawn item '{slug}' for language '{lang}': "
                            "its directory or items subdirectory is a symlink or junction; "
                            "reconcile its artifacts manually."
                        )
                        continue
                    rel_p = pathlib.Path(lang) / "items" / f"{slug}.json"
                    item_file_path = export_dir / rel_p
                    if item_file_path.exists():
                        backup_path = backup_dir / rel_p
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(item_file_path, backup_path)
                        promoted_actions.append({"type": "delete", "rel_path": rel_p})

                # 2. Clean up any monthly archives that are no longer present in staging but were affected
                for lang in config.target_languages:
                    for month in affected_months_by_lang[lang]:
                        archive_rel_path = pathlib.Path(lang) / "archives" / f"archive_{month.replace('-', '_')}.json"
                        if archive_rel_path not in staging_files:
                            archive_path = export_dir / archive_rel_path
                            if archive_path.exists():
                                backup_path = backup_dir / archive_rel_path
                                backup_path.parent.mkdir(parents=True, exist_ok=True)
                                os.replace(archive_path, backup_path)
                                promoted_actions.append({"type": "delete", "rel_path": archive_rel_path})

                    # Check archives/index.json (manifest)
                    manifest_rel_path = pathlib.Path(lang) / "archives" / "index.json"
                    if manifest_rel_path not in staging_files:
                        manifest_path = export_dir / manifest_rel_path
                        if manifest_path.exists():
                            backup_path = backup_dir / manifest_rel_path
                            backup_path.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(manifest_path, backup_path)
                            promoted_actions.append({"type": "delete", "rel_path": manifest_rel_path})

            # 3. Clean up artifacts of languages no longer configured
            # (language-set shrink), in both run and rebuild modes
            # (EXECUTION_POLICY.md section 6.2). A directory is treated as a
            # removed-language directory only when its name still has
            # publish-owned state: publish_language_status rows, which
            # persist as withdrawn. Directory names and generic subdirectory
            # shapes (items/, archives/) are NOT ownership evidence, so
            # directories without publish state are never touched; leftovers
            # from a canonical database reset are cleared by wiping the
            # derived export tree, not by heuristic sweeps. Symlinks and
            # junctions are never followed during this destructive sweep:
            # their targets may live outside the export tree.
            removed_languages = {
                lang
                for lang in repo.get_publish_language_status_languages()
                if lang not in config.target_languages
            }
            for entry in sorted(export_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                if entry.name not in removed_languages:
                    continue
                if _is_symlink_or_reparse_point(entry):
                    logger.warning(
                        f"Skipping cleanup of language directory '{entry.name}': "
                        "it is a symlink or junction; reconcile its artifacts manually."
                    )
                    continue
                stale_artifacts = []
                removed_lang_index = entry / "index.json"
                if removed_lang_index.exists():
                    stale_artifacts.append(removed_lang_index)
                for sub_name in ("items", "archives"):
                    sub_dir = entry / sub_name
                    if sub_dir.exists():
                        if _is_symlink_or_reparse_point(sub_dir):
                            logger.warning(
                                f"Skipping cleanup of '{sub_name}' for language directory '{entry.name}': "
                                "it is a symlink or junction; reconcile its artifacts manually."
                            )
                            continue
                        stale_artifacts.extend(sorted(sub_dir.glob("*.json")))
                for stale_path in stale_artifacts:
                    stale_rel_path = stale_path.relative_to(export_dir)
                    backup_path = backup_dir / stale_rel_path
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stale_path, backup_path)
                    promoted_actions.append({"type": "delete", "rel_path": stale_rel_path})

            # If all promotion succeeds, clean up backup
            if backup_dir.exists():
                shutil.rmtree(backup_dir)

        except Exception:
            # File system Rollback / Restore
            for action in reversed(promoted_actions):
                rel_path = action["rel_path"]
                dest_path = export_dir / rel_path
                backup_path = backup_dir / rel_path

                if action["type"] == "replace":
                    if action["had_existing"]:
                        # Restore original from backup
                        if backup_path.exists():
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(backup_path, dest_path)
                    else:
                        # Delete newly created file
                        dest_path.unlink(missing_ok=True)
                elif action["type"] == "delete":
                    # Restore deleted file from backup
                    if backup_path.exists():
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(backup_path, dest_path)

            # Clean up backup_dir
            if backup_dir.exists():
                try:
                    shutil.rmtree(backup_dir)
                except Exception:
                    pass
            raise

        return {
            "status": "success",
            "published_count": published_count,
            "withdrawn_count": withdrawn_count,
            "errors": []
        }

    except Exception:
        # DB rollback state
        rollback_db_state(conn, db_compensations)
        raise

    finally:
        # Always clean up staging directory
        if staging_dir.exists():
            try:
                shutil.rmtree(staging_dir)
            except Exception:
                pass
        # Close the connection this run opened. sqlite3's internal statement
        # cache forms a reference cycle with the connection, so without an
        # explicit close the file handle is released only at the whim of the
        # cyclic GC (on Windows the locked database file then fails test
        # teardown with PermissionError).
        conn.close()
