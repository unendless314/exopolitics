"""
Deterministic generation plan and content fingerprinting.

After reconciliation leaves the DB in a stable snapshot, a single
deterministic plan describes the planned final bytes of every artifact of the next export
generation: per-language index, archives manifest (always written, empty as
``[]``), monthly archives, item payloads and stats. The same plan drives the
``content_fingerprint`` state comparison and the generation build, so DB
metadata, files and the pointer can never disagree about what "the current
export state" is.

Clock discipline: nothing here calls a clock. The single ``run_ts`` for the
whole run is taken once by the orchestrator (after the process lock is
acquired) and passed in; this module therefore stays compatible with the
test-suite FakeClock patch points.
"""
import hashlib
import json
import pathlib
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from .config import PublishConfig
from .database import PublishRepository
from .digest_index import DigestIndex
from .validation import assemble_item_payload, validate_item_payload

# Versioned fingerprint algorithm identifier, recorded verbatim in
# current.json so a future algorithm upgrade is an explicit rebuild trigger.
FINGERPRINT_ALGORITHM = "sha256-exportstate-v1"

# stats.json key excluded from the fingerprint: it is a run wall-clock field
# and would otherwise change the fingerprint on every successful run.
_STATS_TIMESTAMP_KEY = "last_export_run_timestamp"


def serialize_json_bytes(obj: Any) -> bytes:
    """
    Canonical artifact serialization (``json.dump(obj, f, indent=2,
    ensure_ascii=False)`` with no trailing newline). Byte-stability tests
    and site loaders depend on this exact shape.
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


def _iter_archive_entries(
    repo: PublishRepository,
    batch_size: int,
    language_code: str,
    month: str,
) -> Iterator[Dict[str, Any]]:
    """One monthly archive's entries, fetched in bounded batches and yielded
    row by row: memory stays bounded by the batch size, never by the month's
    item count."""
    offset = 0
    while True:
        rows = repo.fetch_archive_month_batch(language_code, month, batch_size, offset)
        if not rows:
            break
        for row in rows:
            yield {
                "slug": row["slug"],
                "display_title": row["display_title"],
                "summary_short": row["summary_short"],
                "canonical_url": row["canonical_url"],
                "source_published_at": row["source_published_at"],
                "approved_at": row["approved_at"],
                "published_at": row["published_at"],
            }
        offset += len(rows)


def _iter_json_array_bytes(entries: Iterator[Dict[str, Any]]) -> Iterator[bytes]:
    """
    Byte chunks of the canonical JSON array serialization of ``entries``,
    streamed element by element. The joined chunks equal
    ``serialize_json_bytes(list(entries))`` exactly: ``[\\n  `` prefix,
    ``,\\n  `` separators, each element serialized with ``indent=2`` and
    re-indented one level, ``\\n]`` suffix, and ``[]`` when empty.
    """
    iterator = iter(entries)
    try:
        first = next(iterator)
    except StopIteration:
        yield b"[]"
        return
    yield b"[\n  "
    yield json.dumps(first, indent=2, ensure_ascii=False).replace("\n", "\n  ").encode("utf-8")
    for entry in iterator:
        yield b",\n  "
        yield json.dumps(entry, indent=2, ensure_ascii=False).replace("\n", "\n  ").encode("utf-8")
    yield b"\n]"


def _hash_json_array_stream(chunks: Iterator[bytes]) -> str:
    """sha256 hex digest over a chunk stream, without joining it in memory."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def _hash_file_bytes(path: pathlib.Path) -> str:
    """sha256 hex digest of a file, read in fixed-size blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


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
    planned_digest: str,
    prior_digest_for: Callable[[str], Optional[str]],
    fallback_root: Optional[pathlib.Path],
    run_ts: str,
    rebuild: bool,
) -> str:
    """
    Planned manifest ``updated_at`` for one active month, by priority:

    1. rebuild -> run_ts (forced full refresh).
    2. metadata row missing -> run_ts (heal: the row predates v002 or was
       lost; the runner stamps what this plan writes).
    3. live generation hash stream digest matches the planned digest -> keep
       the recorded DB value (content unchanged, timestamp must not advance).
    4. hash missing -> hash-compare against the fallback root (the live
       generation root, set only when a pointer exists); an equal digest
       keeps the DB value.
    5. anything else -> run_ts.
    """
    if rebuild:
        return run_ts
    meta_row = repo.get_archive_metadata(language_code, month)
    if meta_row is None:
        return run_ts
    rel_path = f"{language_code}/archives/{archive_file_name(month)}"
    recorded = prior_digest_for(rel_path)
    if recorded is not None:
        return meta_row["updated_at"] if recorded == f"sha256:{planned_digest}" else run_ts
    if fallback_root is not None:
        fallback_file = fallback_root / rel_path
        if fallback_file.is_file() and _hash_file_bytes(fallback_file) == planned_digest:
            return meta_row["updated_at"]
    return run_ts


def build_generation_plan(
    repo: PublishRepository,
    config: PublishConfig,
    prior_digest_for: Callable[[str], Optional[str]],
    digest_index: DigestIndex,
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
    during the fingerprint pass. The fingerprint pass also appends every
    planned (path, digest) to ``digest_index`` (digest carry-over), so the
    write pass can link reused artifacts without re-serializing them or
    re-reading their database rows.
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
        # --- Latest index (batched; the entry shape is contractually fixed)
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
            digest = _hash_json_array_stream(_iter_json_array_bytes(
                _iter_archive_entries(repo, batch_size, lang, month)
            ))
            archive_hashes[(lang, month)] = digest
            archive_stamps[(lang, month)] = _decide_archive_stamp(
                repo, lang, month, digest,
                prior_digest_for, fallback_root, run_ts, rebuild,
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

    # --- Global stats (construction and key order are contractually fixed)
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
    return plan, compute_content_fingerprint(plan, repo, config, digest_index)


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
    digest_index: Optional[DigestIndex] = None,
) -> str:
    """
    Versioned SHA-256 over the planned export state: a header pinning the
    algorithm, coverage policy, latest limit, archive granularity and the
    configured languages, then every artifact's ``rel_path\\0sha256\\0`` in
    fixed order. stats.json enters without ``last_export_run_timestamp`` so
    run wall-clock never perturbs the comparison.

    When ``digest_index`` is given, every planned (rel_path, digest) pair is
    also appended to it in bounded batches (digest carry-over): the write
    pass consumes the same digests via ``DigestIndex.iter_planned`` instead
    of re-serializing every artifact. Per the plan's dual-digest rule the
    stats.json entry recorded here is the excluded-timestamp variant, so it
    can never equal a prior real-bytes digest and stats.json is always
    physically written.
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
    pending: List[Tuple[str, str]] = []
    for rel_path, artifact_digest in _iter_planned_artifact_digests(
        plan, repo, config, exclude_stats_timestamp=True
    ):
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(artifact_digest.encode("ascii"))
        digest.update(b"\0")
        if digest_index is not None:
            pending.append((rel_path, artifact_digest))
            if len(pending) >= config.execution_policy.batch_size:
                digest_index.add_planned_batch(pending)
                pending.clear()
    if digest_index is not None and pending:
        digest_index.add_planned_batch(pending)
    return f"{FINGERPRINT_ALGORITHM}:{digest.hexdigest()}"


def planned_chunks_for(
    plan: GenerationPlan,
    repo: PublishRepository,
    config: PublishConfig,
    rel_path: str,
) -> Iterator[bytes]:
    """
    The planned bytes of one artifact as a chunk stream, produced on demand
    for the write pass. Only artifacts that need a physical write are ever
    materialized: reused artifacts are hardlinked from the trusted prior
    generation and never reach this function. Item payloads are re-read
    individually by slug (one row per changed item) and re-validated at the
    payload boundary; monthly archives are re-streamed entry by entry, so no
    artifact larger than one payload (or one archive batch) is ever resident
    in memory. A path outside the fixed artifact grammar, a month absent
    from the plan, or a missing item row inside the held snapshot
    transaction is a runner bug and raises.
    """
    if rel_path == "stats.json":
        # Real written bytes, including last_export_run_timestamp (the
        # excluded-timestamp variant exists only inside content_fingerprint).
        yield serialize_json_bytes(plan.stats)
        return
    for lang in plan.index_entries:
        if rel_path == f"{lang}/index.json":
            yield serialize_json_bytes(plan.index_entries[lang])
            return
        if rel_path == f"{lang}/archives/index.json":
            yield serialize_json_bytes(plan.manifest_entries[lang])
            return
        archives_prefix = f"{lang}/archives/"
        if rel_path.startswith(archives_prefix):
            file_name = rel_path[len(archives_prefix):]
            if file_name.startswith("archive_") and file_name.endswith(".json"):
                month = file_name[len("archive_"):-len(".json")].replace("_", "-")
                if (lang, month) in plan.archive_hashes:
                    yield from _iter_json_array_bytes(_iter_archive_entries(
                        repo, config.execution_policy.batch_size, lang, month
                    ))
                    return
        items_prefix = f"{lang}/items/"
        if rel_path.startswith(items_prefix) and rel_path.endswith(".json"):
            slug = rel_path[len(items_prefix):-len(".json")]
            row = repo.fetch_published_payload_by_slug(lang, slug)
            if row is None:
                raise RuntimeError(
                    f"Planned item {rel_path} has no published payload row in the "
                    "held snapshot; the plan and the database disagree, which is "
                    "a runner bug, not a recoverable state."
                )
            payload = assemble_item_payload(dict(row), row["slug"], row["published_at"])
            validate_item_payload(payload)
            yield serialize_json_bytes(payload)
            return
    raise ValueError(f"Path outside the planned artifact grammar: {rel_path!r}")
