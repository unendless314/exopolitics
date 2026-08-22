"""
Generation store: pointer IO, generation ids, staging-to-generations moves,
hardlink reuse and retention.

Readers enter exclusively through ``current.json``; generation directories
under ``generations/`` are immutable once moved in. The pointer is switched
with a same-volume ``os.replace`` as the last step of a build; a no-change
run only refreshes ``last_successful_run_at`` atomically.

Hardlink reuse: a content-changing build writes physical bytes only for
new or changed artifacts; an unchanged artifact is ``os.link()``-ed from the
trusted preceding generation when its planned digest matches the digest
recorded in that generation's ``file_hashes.jsonl`` stream. The reuse
decision trusts the recorded hashes and never re-hashes source bytes at link
time — re-hashing would negate the saving, and generations are immutable
with fail-stop handling for missing or corrupt hash metadata. The accepted
residual risk is an integrity risk under the single-operator deployment:
out-of-band corruption of a live-generation file (bit rot, manual edit, a
bad restore) propagates into future generations through hardlinks instead of
being healed, and a stream record forged to match planned content would link
bytes the plan did not produce. Neither case is detectable without
re-hashing; the repair path is ``rebuild``, which physically rewrites
everything and re-establishes verified bytes.

Every link is verified after the fact: destination and source must resolve
to the same ``(st_dev, st_ino)`` and the source must still be a regular,
non-reparse file inside the trusted prior generation; a mismatch removes the
destination and fails stop. This is the portable backstop for the
check-then-act gap between source validation and linking.

Immutability is safety-critical: generation contents must never be
overwritten, truncated, chmod-ed or replaced in place — with hardlink
reuse, an in-place edit silently rewrites every generation sharing the
inode.

Pointer validation is fail-stop: a corrupt ``current.json`` (unparseable
JSON, missing fields, a generation id outside the strict Windows-safe
format, a calendar-impossible timestamp, an empty ``languages`` list, or a
generation directory that does not exist) raises instead of
being silently treated as absent. Generation ids are validated before ever
being joined into a path, so no arbitrary string reaches the filesystem.
"""
import datetime
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import sqlite3
import stat
import time
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from .digest_index import DigestIndex
from .generation import serialize_json_bytes

logger = logging.getLogger("publish.generation_store")

POINTER_FILE_NAME = "current.json"
GENERATIONS_DIR_NAME = "generations"
STAGING_DIR_NAME = ".staging"
RETAINED_GENERATION_COUNT = 5

# The per-generation hash stream: newline-delimited JSON records, one per
# artifact in fixed artifact order, referenced from meta.json.
HASH_STREAM_NAME = "file_hashes.jsonl"

# Strict Windows-safe generation id: ISO-8601 UTC second precision with
# colons replaced by hyphens, plus an optional same-second collision suffix.
GENERATION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$")
_ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_FINGERPRINT_RE = re.compile(r"^sha256-exportstate-v1:[0-9a-f]{64}$")
_STREAM_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_POINTER_WRITE_MAX_ATTEMPTS = 5
_POINTER_WRITE_RETRY_DELAY_SECONDS = 0.1


def is_valid_generation_id(value: Any) -> bool:
    return isinstance(value, str) and GENERATION_ID_RE.match(value) is not None


def is_valid_iso_timestamp(value: Any) -> bool:
    """Format plus calendar validity: the regex pins the shape, strptime
    rejects impossible dates (e.g. February 30) that the shape admits."""
    if not isinstance(value, str) or _ISO_TIMESTAMP_RE.match(value) is None:
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def generation_id_from_timestamp(timestamp: str) -> str:
    """ISO UTC timestamp -> generation id base (colons become hyphens)."""
    return timestamp.replace(":", "-")


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


def _is_legal_artifact_path(value: Any) -> bool:
    """A generation-relative artifact path: a non-empty forward-slash
    separated string with no leading slash, no backslashes or drive
    letters, and no empty, ``.`` or ``..`` segments."""
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/") or "\\" in value or ":" in value:
        return False
    return all(segment not in ("", ".", "..") for segment in value.split("/"))


def generation_root_for(export_dir: pathlib.Path, generation: str) -> pathlib.Path:
    """
    Resolve a generation id to its directory. The id is validated against
    the strict format first, so no arbitrary string is ever joined into a
    path (the regex admits no separators or parent references).
    """
    if not is_valid_generation_id(generation):
        raise ValueError(f"Invalid generation id: {generation!r}")
    return export_dir / GENERATIONS_DIR_NAME / generation


def validate_pointer(pointer: Any, export_dir: pathlib.Path) -> None:
    """
    Fail-stop validation of a parsed current.json: required fields, strict
    generation id format, and the referenced generation directory must
    exist and be a directory.
    """
    if not isinstance(pointer, dict):
        raise RuntimeError("current.json is invalid: top-level value must be an object")
    generation = pointer.get("generation")
    if not is_valid_generation_id(generation):
        raise RuntimeError(
            f"current.json is invalid: 'generation' must match "
            f"{GENERATION_ID_RE.pattern}, got {generation!r}"
        )
    for field in ("export_completed_at", "last_successful_run_at"):
        if not is_valid_iso_timestamp(pointer.get(field)):
            raise RuntimeError(
                f"current.json is invalid: '{field}' must be an ISO-8601 UTC "
                f"timestamp, got {pointer.get(field)!r}"
            )
    languages = pointer.get("languages")
    if (
        not isinstance(languages, list)
        or not languages
        or not all(isinstance(lang, str) for lang in languages)
    ):
        raise RuntimeError(
            "current.json is invalid: 'languages' must be a non-empty list of strings"
        )
    fingerprint = pointer.get("content_fingerprint")
    if not isinstance(fingerprint, str) or _FINGERPRINT_RE.match(fingerprint) is None:
        raise RuntimeError(
            "current.json is invalid: 'content_fingerprint' must be a "
            "sha256-exportstate-v1 digest string"
        )
    generation_root = generation_root_for(export_dir, generation)
    if not generation_root.is_dir():
        raise RuntimeError(
            f"current.json points at generation '{generation}', but "
            f"{generation_root} does not exist or is not a directory"
        )


def read_pointer(export_dir: pathlib.Path) -> Optional[Dict[str, Any]]:
    """
    Read and fully validate current.json. Returns None only when the file
    does not exist (fresh export root awaiting bootstrap); any
    corrupt content raises.
    """
    pointer_path = export_dir / POINTER_FILE_NAME
    if not pointer_path.exists():
        return None
    try:
        raw = pointer_path.read_text(encoding="utf-8")
        pointer = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError(f"current.json is unreadable or corrupt: {e}") from e
    validate_pointer(pointer, export_dir)
    return pointer


def write_pointer_atomic(export_dir: pathlib.Path, pointer: Dict[str, Any]) -> None:
    """
    Switch current.json atomically: write a sibling temp file, then
    os.replace it over the pointer (same volume, single-file atomic replace,
    Windows-safe). On a sharing violation (PermissionError) the old pointer
    stays valid; the replace is retried a limited number of times before the
    run fails stop.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    payload = serialize_json_bytes(pointer)
    temp_path = export_dir / f".{POINTER_FILE_NAME}.tmp"
    pointer_path = export_dir / POINTER_FILE_NAME
    attempt = 0
    while True:
        try:
            with open(temp_path, "wb") as f:
                f.write(payload)
            os.replace(temp_path, pointer_path)
            return
        except PermissionError:
            attempt += 1
            if attempt >= _POINTER_WRITE_MAX_ATTEMPTS:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            logger.warning(
                f"Pointer switch blocked by a sharing violation "
                f"(attempt {attempt}/{_POINTER_WRITE_MAX_ATTEMPTS}); retrying."
            )
            time.sleep(_POINTER_WRITE_RETRY_DELAY_SECONDS)


def allocate_generation_id(generations_dir: pathlib.Path, run_ts: str) -> str:
    """
    Generation id for this run: the run timestamp with colons replaced. On
    same-second collisions the ``-rN`` suffix continues after the highest
    surviving suffix rather than refilling gaps left by retention — reusing
    a retired id would make a fresh build sort as the oldest, so a later
    sweep could delete it while keeping genuinely older generations.
    """
    base = generation_id_from_timestamp(run_ts)
    highest = 0
    if generations_dir.is_dir():
        prefix = f"{base}-r"
        for entry in generations_dir.iterdir():
            if entry.name == base:
                highest = max(highest, 1)
            elif entry.name.startswith(prefix) and entry.name[len(prefix):].isdigit():
                highest = max(highest, int(entry.name[len(prefix):]))
    if highest == 0:
        return base
    return f"{base}-r{highest + 1}"


def _trusted_prior_root(prior_root: pathlib.Path) -> Optional[pathlib.Path]:
    """The resolved trusted prior generation root, or None when it fails
    validation (not a directory, or itself a symlink/junction). Validation
    failure disables reuse for the whole build: every artifact falls back to
    a physical write, never a fail-stop."""
    if _is_symlink_or_reparse_point(prior_root) or not prior_root.is_dir():
        logger.warning(
            f"Prior generation root {prior_root} failed validation (not a "
            "plain directory); hardlink reuse is disabled for this build."
        )
        return None
    return prior_root.resolve()


def _is_valid_link_source(prior_root_resolved: pathlib.Path, source: pathlib.Path) -> bool:
    """A link source must exist as a regular, non-reparse file whose
    resolved path stays beneath the resolved trusted prior generation root —
    a symlink or junction in a parent directory must not be able to make a
    regular-looking source file escape the trusted tree. Anything else
    degrades safely to a physical write by the caller."""
    try:
        if not stat.S_ISREG(os.lstat(source).st_mode):
            return False
    except OSError:
        return False
    if _is_symlink_or_reparse_point(source):
        return False
    try:
        source.resolve().relative_to(prior_root_resolved)
    except (OSError, ValueError):
        return False
    return True


def _link_verified(
    prior_root_resolved: pathlib.Path,
    source: pathlib.Path,
    destination: pathlib.Path,
) -> bool:
    """Post-link verification: destination and source must resolve to the
    same (st_dev, st_ino), and the source must still pass link-source
    validation. This is the portable backstop for the check-then-act gap
    between source validation and os.link()."""
    try:
        destination_stat = os.stat(destination)
        source_stat = os.stat(source)
    except OSError:
        return False
    if (destination_stat.st_dev, destination_stat.st_ino) != (
        source_stat.st_dev,
        source_stat.st_ino,
    ):
        return False
    return _is_valid_link_source(prior_root_resolved, source)


def _try_link_artifact(
    prior_root: pathlib.Path,
    prior_root_resolved: pathlib.Path,
    rel_path: str,
    destination: pathlib.Path,
) -> bool:
    """
    Attempt a verified hardlink reuse of an unchanged artifact from the
    trusted prior generation. Returns False when the source fails validation
    or os.link fails for any reason (cross-volume placement, filesystem
    policy, NTFS limitation, network storage) — the caller then falls back
    to a physical write. A post-link verification mismatch is different in
    kind: it removes the destination and raises (fail-stop), because a link
    that does not resolve to the same inode as a validated source means
    cross-generation contamination, not a plain unavailability of linking.
    """
    source = prior_root / rel_path
    if not _is_valid_link_source(prior_root_resolved, source):
        return False
    try:
        os.link(source, destination)
    except OSError:
        return False
    if _link_verified(prior_root_resolved, source, destination):
        return True
    try:
        destination.unlink()
    except OSError:
        pass
    raise RuntimeError(
        f"Post-link verification failed for {destination}: the linked file "
        "does not resolve to the same (st_dev, st_ino) as a still-valid "
        "source inside the trusted prior generation."
    )


def write_generation_to_staging(
    export_dir: pathlib.Path,
    *,
    planned_entries: Iterable[Tuple[str, str]],
    chunks_for: Callable[[str], Iterator[bytes]],
    prior_root: Optional[pathlib.Path],
    digest_index: DigestIndex,
    force_full_write: bool,
    generation: str,
    created_at: str,
    content_fingerprint: str,
    languages: List[str],
) -> pathlib.Path:
    """
    Write a complete generation into ``.staging`` and move it into
    ``generations/<generation>``. Every configured language gets its
    ``items/`` and ``archives/`` directories even when empty (bootstrap
    layout), and the archives manifest is always written by the plan (empty
    as ``[]``). Returns the new generation root.

    ``planned_entries`` are the fingerprint pass's (rel_path, planned
    digest) pairs in fixed artifact order (digest carry-over via the
    run's disk-backed digest index). Per entry, the reuse decision is:

    - reuse with ``os.link()`` iff not ``force_full_write``, ``prior_root``
      is a valid trusted generation root, the prior generation's recorded
      digest for the path equals the planned digest, and the source passes
      link validation — with post-link verification on every link;
    - otherwise physically write ``chunks_for(rel_path)`` chunk by chunk
      (changed or new artifacts, any link failure fallback, and
      ``rebuild``'s mandatory full physical rewrite).

    The reuse decision trusts the hashes recorded in the prior generation's
    hash stream; it never re-hashes source bytes (see the module docstring
    for the accepted integrity risk and the ``rebuild`` repair path). One
    JSONL record per artifact is appended to the staging
    ``file_hashes.jsonl`` as it is processed (the write side stays
    memory-bounded); ``meta.json`` keeps the scalar fields plus the
    ``file_hashes`` reference and is written last. Digests recorded in the
    stream are of the actual written bytes; a linked artifact records the
    planned digest, equal to the prior recorded digest by construction.

    Generation contents are immutable once moved in — safety-critical under
    hardlink reuse: an in-place edit would silently rewrite every generation
    sharing the inode. A builder must only ever create new destination
    files.
    """
    if not is_valid_generation_id(generation):
        raise ValueError(f"Invalid generation id: {generation!r}")
    staging_dir = export_dir / STAGING_DIR_NAME
    if staging_dir.exists():
        if _is_symlink_or_reparse_point(staging_dir):
            raise RuntimeError(
                f"Refusing to clear staging directory {staging_dir}: "
                "it is a symlink or junction; reconcile it manually."
            )
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    prior_root_resolved: Optional[pathlib.Path] = None
    if not force_full_write and prior_root is not None:
        prior_root_resolved = _trusted_prior_root(prior_root)

    stream_path = staging_dir / HASH_STREAM_NAME
    # newline="\n" pins LF line endings: the default text mode would write
    # CRLF on Windows, and the stream is read line-by-line as JSONL.
    with open(stream_path, "w", encoding="utf-8", newline="\n") as stream:
        for rel_path, planned_digest in planned_entries:
            destination = staging_dir / rel_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (
                prior_root_resolved is not None
                and digest_index.prior_digest_for(rel_path) == f"sha256:{planned_digest}"
                and _try_link_artifact(prior_root, prior_root_resolved, rel_path, destination)
            ):
                recorded = f"sha256:{planned_digest}"
            else:
                hasher = hashlib.sha256()
                with open(destination, "wb") as f:
                    for chunk in chunks_for(rel_path):
                        f.write(chunk)
                        hasher.update(chunk)
                recorded = f"sha256:{hasher.hexdigest()}"
            stream.write(json.dumps({"path": rel_path, "digest": recorded}) + "\n")

    for lang in languages:
        (staging_dir / lang / "items").mkdir(parents=True, exist_ok=True)
        (staging_dir / lang / "archives").mkdir(parents=True, exist_ok=True)

    meta = {
        "generation": generation,
        "created_at": created_at,
        "content_fingerprint": content_fingerprint,
        "file_hashes": HASH_STREAM_NAME,
    }
    with open(staging_dir / "meta.json", "wb") as f:
        f.write(serialize_json_bytes(meta))

    generations_dir = export_dir / GENERATIONS_DIR_NAME
    generations_dir.mkdir(exist_ok=True)
    generation_root = generations_dir / generation
    os.rename(staging_dir, generation_root)
    return generation_root


def discard_staging(export_dir: pathlib.Path) -> None:
    """Best-effort staging cleanup for run teardown; never raises."""
    staging_dir = export_dir / STAGING_DIR_NAME
    try:
        if staging_dir.exists() and not _is_symlink_or_reparse_point(staging_dir):
            shutil.rmtree(staging_dir)
    except OSError:
        pass


def _matches_legacy_meta_shape(meta: Dict[str, Any]) -> bool:
    """
    Positive witness for a legacy pre-stream meta.json, consulted solely as a
    format witness (its hashes are never used for reuse): strict generation
    id, calendar-valid ``created_at``, well-formed ``content_fingerprint``,
    and a non-empty ``aggregate_file_hashes`` table whose keys are legal
    generation-relative paths and whose values are ``sha256:<64 hex>``
    digests. This is corruption disambiguation, not a security boundary:
    even a forged but valid-looking witness only routes the run to the safe
    no-reuse path, so its failure mode cannot produce wrong output.
    """
    if not is_valid_generation_id(meta.get("generation")):
        return False
    if not is_valid_iso_timestamp(meta.get("created_at")):
        return False
    fingerprint = meta.get("content_fingerprint")
    if not isinstance(fingerprint, str) or _FINGERPRINT_RE.match(fingerprint) is None:
        return False
    hashes = meta.get("aggregate_file_hashes")
    if not isinstance(hashes, dict) or not hashes:
        return False
    return all(
        _is_legal_artifact_path(path)
        and isinstance(digest, str)
        and _STREAM_DIGEST_RE.match(digest) is not None
        for path, digest in hashes.items()
    )


def _load_hash_stream(stream_path: pathlib.Path, digest_index: DigestIndex) -> None:
    """
    Stream the live generation's hash stream into the digest index's prior
    table, line by line, validated as it is read (EXECUTION_POLICY Section
    9: no whole-document parse, no resident structure proportional to item
    count). Fail-stop on: a missing or empty stream (every legitimately
    built generation records at least stats.json and the per-language
    aggregate files), a malformed record, an illegal or duplicate path, or a
    final record that is not stats.json (suffix truncation detection —
    stats.json is always the last artifact in the fixed order, so even a
    valid-prefix truncation that lands on a line boundary is caught).

    Link source paths always come from the plan, never from this stream;
    path validation here is corruption detection. Duplicate detection is
    delegated to the prior table's PRIMARY KEY so no resident set of seen
    paths is needed.
    """
    if not stream_path.is_file():
        raise RuntimeError(
            f"Live generation meta.json references {HASH_STREAM_NAME}, but "
            f"{stream_path} does not exist; the export state is corrupt and "
            "needs manual reconciliation."
        )
    count = 0
    last_path: Optional[str] = None
    try:
        with open(stream_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"Live generation {HASH_STREAM_NAME} has a malformed "
                        f"record at line {count + 1}: {e}"
                    ) from e
                path = record.get("path") if isinstance(record, dict) else None
                digest = record.get("digest") if isinstance(record, dict) else None
                if (
                    not _is_legal_artifact_path(path)
                    or not isinstance(digest, str)
                    or _STREAM_DIGEST_RE.match(digest) is None
                ):
                    raise RuntimeError(
                        f"Live generation {HASH_STREAM_NAME} has an invalid "
                        f"record at line {count + 1}: {stripped!r}"
                    )
                count += 1
                last_path = path
                try:
                    digest_index.add_prior(path, digest)
                except sqlite3.IntegrityError as e:
                    raise RuntimeError(
                        f"Live generation {HASH_STREAM_NAME} repeats the "
                        f"artifact path {path!r}."
                    ) from e
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(
            f"Live generation {HASH_STREAM_NAME} is unreadable: {e}"
        ) from e
    if count == 0:
        raise RuntimeError(
            f"Live generation {HASH_STREAM_NAME} is empty; every legitimately "
            "built generation records at least stats.json and the per-language "
            "aggregate files, so an empty stream is corruption, not a "
            "zero-data state."
        )
    if last_path != "stats.json":
        raise RuntimeError(
            f"Live generation {HASH_STREAM_NAME} ends at {last_path!r} instead "
            "of stats.json; the stream is truncated or reordered."
        )


def load_live_generation_hashes(live_root: pathlib.Path, digest_index: DigestIndex) -> bool:
    """
    Load the live generation's recorded artifact hashes into the run's
    temporary digest index for stamping and reuse lookups. Returns True when
    the live generation is a legacy pre-stream one (a meta.json positively
    matching the old aggregate shape): such a generation carries no reuse
    information — the run logs a notice and proceeds with an empty prior
    table, so archive stamping falls back to the byte-compare against the
    live generation and the first content-changing build physically writes
    every artifact and establishes the full stream.

    Fail-stop states (never silent rebuild triggers): a missing or
    unparseable meta.json; a present ``file_hashes`` field whose value is
    not exactly ``file_hashes.jsonl`` — a JSON ``null`` counts: a genuine
    legacy meta.json never carries the field at all, so a null marks a
    damaged newer file even when a valid-looking aggregate table survives
    next to it; a referenced stream that is missing, empty, malformed, or
    truncated; and a meta.json without the field that fails the legacy
    witness checks (for example a file whose reference field was lost).
    """
    meta_path = live_root / "meta.json"
    if not meta_path.is_file():
        raise RuntimeError(
            f"Live generation {live_root} is missing meta.json; "
            "the export state is corrupt and needs manual reconciliation."
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Live generation meta.json is unreadable or corrupt: {e}") from e
    if not isinstance(meta, dict):
        raise RuntimeError("Live generation meta.json is invalid: top-level value must be an object.")
    if "file_hashes" in meta:
        reference = meta["file_hashes"]
        if reference != HASH_STREAM_NAME:
            raise RuntimeError(
                f"Live generation meta.json has an unexpected file_hashes "
                f"reference: {reference!r} (expected {HASH_STREAM_NAME!r})."
            )
        _load_hash_stream(live_root / HASH_STREAM_NAME, digest_index)
        return False
    if _matches_legacy_meta_shape(meta):
        logger.info(
            "Live generation %s carries a legacy pre-stream meta.json; "
            "treating it as having no reuse information. The next "
            "content-changing build physically writes every artifact and "
            "establishes the full hash stream.",
            live_root.name,
        )
        return True
    raise RuntimeError(
        "Live generation meta.json does not reference a hash stream and "
        "does not positively match the legacy aggregate shape (strict "
        "generation id, calendar-valid created_at, well-formed "
        "content_fingerprint and a non-empty aggregate_file_hashes table); "
        "the export state is corrupt and needs manual reconciliation."
    )


def _tree_contains_reparse_point(root: pathlib.Path) -> bool:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            if _is_symlink_or_reparse_point(pathlib.Path(dirpath) / name):
                return True
    return False


def _generation_sort_key(name: str) -> Tuple[str, int]:
    """Chronological sort key for a valid generation id: the timestamp
    portion sorts lexicographically (ISO zero-padding is chronological), the
    same-second collision suffix numerically — a plain string sort would
    order ``-r10`` before ``-r2`` and let retention delete newer snapshots.
    """
    if "-r" in name:
        base, suffix = name.rsplit("-r", 1)
        return (base, int(suffix))
    return (name, 0)


def sweep_retired_generations(
    export_dir: pathlib.Path,
    *,
    keep: int = RETAINED_GENERATION_COUNT,
    protected_generation: Optional[str] = None,
) -> None:
    """
    Delete all but the newest ``keep`` generations. The generation the live
    pointer references is never deleted, even in pathological orderings.
    Symlink/junction generations, or trees containing one, are skipped with
    a warning (never delete through a link); deletion failures (locked or
    read-only files held by a reader) are warn-only so a successful run
    never fails in retention.
    """
    generations_dir = export_dir / GENERATIONS_DIR_NAME
    if not generations_dir.is_dir():
        return
    entries: List[pathlib.Path] = []
    for entry in generations_dir.iterdir():
        if not is_valid_generation_id(entry.name):
            logger.warning(
                f"Skipping unrecognized entry in generations directory: {entry.name}"
            )
            continue
        entries.append(entry)
    entries.sort(key=lambda p: _generation_sort_key(p.name))
    retirees = entries[:-keep] if len(entries) > keep else []
    for entry in retirees:
        if entry.name == protected_generation:
            continue
        try:
            if _is_symlink_or_reparse_point(entry) or _tree_contains_reparse_point(entry):
                logger.warning(
                    f"Skipping retention deletion of generation '{entry.name}': "
                    "it is or contains a symlink or junction; reconcile it manually."
                )
                continue
            shutil.rmtree(entry)
            logger.info(f"Retired generation '{entry.name}'.")
        except OSError as e:
            logger.warning(
                f"Could not delete retired generation '{entry.name}': {e}. "
                "It will be retried on a future run."
            )
