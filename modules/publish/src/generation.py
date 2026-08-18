"""
Deterministic generation plan and content fingerprinting (Phase B1).

Part of the generation + atomic pointer refactor
(known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md). After
reconciliation leaves the DB in a stable snapshot, a single deterministic
plan describes the planned final bytes of every artifact of the next export
generation: per-language index, archives manifest (always written, empty as
``[]``), monthly archives, item payloads and stats. The same plan drives the
``content_fingerprint`` state comparison, the generation build and the
one-time flat-layout migration verification, so DB metadata, files and the
pointer can never disagree about what "the current export state" is.

Clock discipline: nothing here calls a clock. The single ``run_ts`` for the
whole run is taken once by the orchestrator (after the process lock is
acquired) and passed in; this module therefore stays compatible with the
test-suite FakeClock patch points.
"""
import hashlib
import json
import pathlib
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .config import PublishConfig
from .database import PublishRepository
from .validation import assemble_item_payload, validate_item_payload

# Versioned fingerprint algorithm identifier, recorded verbatim in
# current.json so a future algorithm upgrade is an explicit rebuild trigger.
FINGERPRINT_ALGORITHM = "sha256-exportstate-v1"

# stats.json key excluded from the fingerprint: it is a run wall-clock field
# and would otherwise change the fingerprint on every successful run.
_STATS_TIMESTAMP_KEY = "last_export_run_timestamp"


def serialize_json_bytes(obj: Any) -> bytes:
    """
    Canonical artifact serialization, byte-identical to the pre-B1 runner
    (``json.dump(obj, f, indent=2, ensure_ascii=False)`` with no trailing
    newline). Migration byte-comparison and Phase A acceptance both depend
    on this exact shape.
    """
    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


def archive_file_name(month: str) -> str:
    return f"archive_{month.replace('-', '_')}.json"


class GenerationPlan:
    """
    Planned final state of one export generation.

    Holds everything the fingerprint, the generation build and the archive
    metadata sync need, so all three always agree:

    - ``index_entries``: lang -> latest-index entry list (bounded by
      ``latest_limit``).
    - ``archive_months``: lang -> active months, sorted ASC (artifact order).
    - ``archive_hashes``: (lang, month) -> sha256 hex of the planned archive
      file bytes.
    - ``archive_stamps``: (lang, month) -> planned manifest ``updated_at``.
    - ``manifest_entries``: lang -> manifest entry list, months DESC.
    - ``stats``: stats.json dict including ``last_export_run_timestamp``.
    """

    def __init__(
        self,
        *,
        index_entries: Dict[str, List[Dict[str, Any]]],
        archive_months: Dict[str, List[str]],
        archive_hashes: Dict[Tuple[str, str], str],
        archive_stamps: Dict[Tuple[str, str], str],
        manifest_entries: Dict[str, List[Dict[str, Any]]],
        stats: Dict[str, Any],
    ) -> None:
        self.index_entries = index_entries
        self.archive_months = archive_months
        self.archive_hashes = archive_hashes
        self.archive_stamps = archive_stamps
        self.manifest_entries = manifest_entries
        self.stats = stats


def _stream_archive_entries(
    repo: PublishRepository,
    batch_size: int,
    language_code: str,
    month: str,
) -> List[Dict[str, Any]]:
    """One monthly archive's entry list, fetched in bounded batches."""
    entries: List[Dict[str, Any]] = []
    offset = 0
    while True:
        rows = repo.fetch_archive_month_batch(language_code, month, batch_size, offset)
        if not rows:
            break
        for row in rows:
            entries.append({
                "slug": row["slug"],
                "display_title": row["display_title"],
                "summary_short": row["summary_short"],
                "canonical_url": row["canonical_url"],
                "source_published_at": row["source_published_at"],
                "approved_at": row["approved_at"],
                "published_at": row["published_at"],
            })
        offset += len(rows)
    return entries


def _iter_item_payloads(
    repo: PublishRepository,
    config: PublishConfig,
    language_code: str,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """
    Stream (slug, assembled payload) for every active published item in one
    language, slug ASC. Payloads are validated here so both the fingerprint
    pass and the write pass enforce the export contract.
    """
    batch_size = config.execution_policy.batch_size
    offset = 0
    while True:
        rows = repo.fetch_published_payload_batch(language_code, batch_size, offset)
        if not rows:
            break
        for row in rows:
            payload = assemble_item_payload(dict(row), row["slug"], row["published_at"])
            validate_item_payload(payload)
            yield row["slug"], payload
        offset += len(rows)


def _decide_archive_stamp(
    repo: PublishRepository,
    language_code: str,
    month: str,
    planned_bytes: bytes,
    planned_digest: str,
    current_hashes: Dict[str, str],
    fallback_root: Optional[pathlib.Path],
    run_ts: str,
    rebuild: bool,
) -> str:
    """
    Planned manifest ``updated_at`` for one active month, by priority:

    1. rebuild -> run_ts (forced full refresh).
    2. metadata row missing -> run_ts (heal: the row predates v002 or was
       lost; the runner stamps what this plan writes).
    3. live generation meta.json hash matches the planned bytes -> keep the
       recorded DB value (content unchanged, timestamp must not advance).
    4. hash missing (e.g. pre-migration) -> byte-compare against the
       fallback root (live generation root, or the flat export tree when no
       pointer exists); equal bytes keep the DB value.
    5. anything else -> run_ts.
    """
    if rebuild:
        return run_ts
    meta_row = repo.get_archive_metadata(language_code, month)
    if meta_row is None:
        return run_ts
    rel_path = f"{language_code}/archives/{archive_file_name(month)}"
    recorded = current_hashes.get(rel_path)
    if recorded is not None:
        return meta_row["updated_at"] if recorded == f"sha256:{planned_digest}" else run_ts
    if fallback_root is not None:
        fallback_file = fallback_root / rel_path
        if fallback_file.is_file() and fallback_file.read_bytes() == planned_bytes:
            return meta_row["updated_at"]
    return run_ts


def build_generation_plan(
    repo: PublishRepository,
    config: PublishConfig,
    current_hashes: Dict[str, str],
    fallback_root: Optional[pathlib.Path],
    run_ts: str,
    rebuild: bool,
) -> Tuple[GenerationPlan, str]:
    """
    Build the deterministic generation plan from the current DB snapshot and
    return (plan, content_fingerprint).

    Archive stamping is decided here, before the fingerprint is computed, so
    the hash always covers the planned final state and a no-change run after
    a build never triggers a spurious rebuild. Memory stays bounded: index
    entries are capped by ``latest_limit``, archives are streamed per month
    (only their digest is kept) and item payloads are streamed per item
    during the fingerprint pass.
    """
    batch_size = config.execution_policy.batch_size
    latest_limit = config.index_policy.latest_limit
    languages = list(config.target_languages.keys())

    index_entries: Dict[str, List[Dict[str, Any]]] = {}
    archive_months: Dict[str, List[str]] = {}
    archive_hashes: Dict[Tuple[str, str], str] = {}
    archive_stamps: Dict[Tuple[str, str], str] = {}
    manifest_entries: Dict[str, List[Dict[str, Any]]] = {}

    for lang in languages:
        # --- Latest index (same batching and entry shape as the pre-B1 runner)
        entries: List[Dict[str, Any]] = []
        offset = 0
        while len(entries) < latest_limit:
            query_limit = min(batch_size, latest_limit - len(entries))
            rows = repo.fetch_latest_index_batch(lang, query_limit, offset)
            if not rows:
                break
            for row in rows:
                entries.append({
                    "slug": row["slug"],
                    "display_title": row["display_title"],
                    "summary_short": row["summary_short"],
                    "canonical_url": row["canonical_url"],
                    "source_published_at": row["source_published_at"],
                    "approved_at": row["approved_at"],
                    "published_at": row["published_at"],
                })
            offset += len(rows)
        index_entries[lang] = entries

        # --- Active months and their stamping decisions
        months = sorted(m for m in repo.get_active_archive_months(lang) if m)
        archive_months[lang] = months
        for month in months:
            archive_bytes = serialize_json_bytes(_stream_archive_entries(repo, batch_size, lang, month))
            digest = hashlib.sha256(archive_bytes).hexdigest()
            archive_hashes[(lang, month)] = digest
            archive_stamps[(lang, month)] = _decide_archive_stamp(
                repo, lang, month, archive_bytes, digest,
                current_hashes, fallback_root, run_ts, rebuild,
            )

        # --- Archives manifest (months DESC, planned updated_at)
        manifest: List[Dict[str, Any]] = []
        for row in repo.get_archive_month_item_counts(lang):
            m_month = row["archive_month"]
            if not m_month:
                continue
            stamp = archive_stamps.get((lang, m_month))
            if stamp is None:
                # Both queries derive from the same active published set; a
                # month without a stamp decision means they disagree, which
                # is a runner bug, not a recoverable state.
                raise RuntimeError(
                    f"Missing archive stamp decision for active month {m_month} lang {lang}"
                )
            manifest.append({
                "archive_month": m_month,
                "file_name": archive_file_name(m_month),
                "item_count": row["item_count"],
                "updated_at": stamp,
            })
        manifest_entries[lang] = manifest

    # --- Global stats (same construction and key order as the pre-B1 runner)
    stats: Dict[str, Any] = {}
    stats["total_active_published_items_by_language"] = repo.count_publish_language_statuses("published")
    for lang in languages:
        if lang not in stats["total_active_published_items_by_language"]:
            stats["total_active_published_items_by_language"][lang] = 0
    stats["total_withdrawn_items_by_language"] = repo.count_publish_language_statuses("withdrawn")
    for lang in languages:
        if lang not in stats["total_withdrawn_items_by_language"]:
            stats["total_withdrawn_items_by_language"][lang] = 0
    stats["latest_index_count_by_language"] = {}
    for lang in languages:
        count = stats["total_active_published_items_by_language"][lang]
        stats["latest_index_count_by_language"][lang] = min(count, latest_limit)
    stats["archive_month_count_by_language"] = {}
    stats["oldest_archive_month_by_language"] = {}
    for lang in languages:
        month_count, oldest_month = repo.get_archive_month_stats(lang)
        stats["archive_month_count_by_language"][lang] = month_count
        stats["oldest_archive_month_by_language"][lang] = oldest_month
    stats["last_export_run_timestamp"] = run_ts

    plan = GenerationPlan(
        index_entries=index_entries,
        archive_months=archive_months,
        archive_hashes=archive_hashes,
        archive_stamps=archive_stamps,
        manifest_entries=manifest_entries,
        stats=stats,
    )
    return plan, compute_content_fingerprint(plan, repo, config)


def _iter_planned_artifact_digests(
    plan: GenerationPlan,
    repo: PublishRepository,
    config: PublishConfig,
    *,
    exclude_stats_timestamp: bool,
) -> Iterator[Tuple[str, str]]:
    """
    (relative path, sha256 hex of planned bytes) for every artifact in the
    fixed fingerprint order: per configured language (config order) the
    index, the archives manifest, each monthly archive (month ASC, digest
    reused from the plan), then every item payload (slug ASC); stats.json
    last.
    """
    for lang in config.target_languages:
        yield f"{lang}/index.json", hashlib.sha256(serialize_json_bytes(plan.index_entries[lang])).hexdigest()
        yield f"{lang}/archives/index.json", hashlib.sha256(serialize_json_bytes(plan.manifest_entries[lang])).hexdigest()
        for month in plan.archive_months[lang]:
            yield f"{lang}/archives/{archive_file_name(month)}", plan.archive_hashes[(lang, month)]
        for slug, payload in _iter_item_payloads(repo, config, lang):
            yield f"{lang}/items/{slug}.json", hashlib.sha256(serialize_json_bytes(payload)).hexdigest()
    stats = plan.stats
    if exclude_stats_timestamp:
        stats = {k: v for k, v in plan.stats.items() if k != _STATS_TIMESTAMP_KEY}
    yield "stats.json", hashlib.sha256(serialize_json_bytes(stats)).hexdigest()


def compute_content_fingerprint(
    plan: GenerationPlan,
    repo: PublishRepository,
    config: PublishConfig,
) -> str:
    """
    Versioned SHA-256 over the planned export state: a header pinning the
    algorithm, coverage policy, latest limit, archive granularity and the
    configured languages, then every artifact's ``rel_path\\0sha256\\0`` in
    fixed order. stats.json enters without ``last_export_run_timestamp`` so
    run wall-clock never perturbs the comparison.
    """
    languages = list(config.target_languages.keys())
    header = "|".join([
        FINGERPRINT_ALGORITHM,
        f"coverage_policy={config.coverage_policy}",
        f"latest_limit={config.index_policy.latest_limit}",
        f"archive_granularity={config.index_policy.archive_granularity}",
        "languages=" + ",".join(languages),
    ])
    digest = hashlib.sha256(header.encode("utf-8") + b"\0")
    for rel_path, artifact_digest in _iter_planned_artifact_digests(
        plan, repo, config, exclude_stats_timestamp=True
    ):
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(artifact_digest.encode("ascii"))
        digest.update(b"\0")
    return f"{FINGERPRINT_ALGORITHM}:{digest.hexdigest()}"


def iter_planned_artifact_bytes(
    plan: GenerationPlan,
    repo: PublishRepository,
    config: PublishConfig,
) -> Iterator[Tuple[str, bytes]]:
    """
    (relative path, planned bytes) for every artifact of the generation, in
    the same fixed order as the fingerprint pass. Shared by the generation
    build and the flat-layout migration verification. stats.json here keeps
    its run timestamp; archives are re-streamed per month so memory stays
    bounded by one month at a time.
    """
    batch_size = config.execution_policy.batch_size
    for lang in config.target_languages:
        yield f"{lang}/index.json", serialize_json_bytes(plan.index_entries[lang])
        yield f"{lang}/archives/index.json", serialize_json_bytes(plan.manifest_entries[lang])
        for month in plan.archive_months[lang]:
            entries = _stream_archive_entries(repo, batch_size, lang, month)
            yield f"{lang}/archives/{archive_file_name(month)}", serialize_json_bytes(entries)
        for slug, payload in _iter_item_payloads(repo, config, lang):
            yield f"{lang}/items/{slug}.json", serialize_json_bytes(payload)
    yield "stats.json", serialize_json_bytes(plan.stats)
