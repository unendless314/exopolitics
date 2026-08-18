"""
Generation store: pointer IO, generation ids, staging-to-generations moves
and retention (Phase B1).

Part of the generation + atomic pointer refactor
(known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md). Readers
enter exclusively through ``current.json``; generation directories under
``generations/`` are immutable once moved in. The pointer is switched with a
same-volume ``os.replace`` as the last step of a build; a no-change run only
refreshes ``last_successful_run_at`` atomically.

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
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .generation import serialize_json_bytes

logger = logging.getLogger("publish.generation_store")

POINTER_FILE_NAME = "current.json"
GENERATIONS_DIR_NAME = "generations"
STAGING_DIR_NAME = ".staging"
RETAINED_GENERATION_COUNT = 5

# Strict Windows-safe generation id: ISO-8601 UTC second precision with
# colons replaced by hyphens, plus an optional same-second collision suffix.
GENERATION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$")
_ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_FINGERPRINT_RE = re.compile(r"^sha256-exportstate-v1:[0-9a-f]{64}$")

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
    does not exist (fresh export root or pending one-time migration); any
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


def _is_aggregate_artifact(rel_path: str) -> bool:
    """Aggregate files are everything except per-item payloads."""
    return "/items/" not in rel_path


def write_generation_to_staging(
    export_dir: pathlib.Path,
    artifacts: Iterable[Tuple[str, bytes]],
    *,
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
    as ``[]``). Aggregate file hashes are recorded in ``meta.json`` as they
    are written. Returns the new generation root.
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

    aggregate_file_hashes: Dict[str, str] = {}
    for rel_path, body in artifacts:
        destination = staging_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "wb") as f:
            f.write(body)
        if _is_aggregate_artifact(rel_path):
            aggregate_file_hashes[rel_path] = f"sha256:{hashlib.sha256(body).hexdigest()}"

    for lang in languages:
        (staging_dir / lang / "items").mkdir(parents=True, exist_ok=True)
        (staging_dir / lang / "archives").mkdir(parents=True, exist_ok=True)

    meta = {
        "generation": generation,
        "created_at": created_at,
        "content_fingerprint": content_fingerprint,
        "aggregate_file_hashes": aggregate_file_hashes,
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


def load_current_generation_hashes(generation_root: pathlib.Path) -> Dict[str, str]:
    """
    Aggregate file hashes recorded in the live generation's meta.json. A
    missing or corrupt meta.json on the live generation is a corrupt state
    and raises (fail-stop), never a silent rebuild trigger.
    """
    meta_path = generation_root / "meta.json"
    if not meta_path.is_file():
        raise RuntimeError(
            f"Live generation {generation_root} is missing meta.json; "
            "the export state is corrupt and needs manual reconciliation."
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Live generation meta.json is unreadable or corrupt: {e}") from e
    hashes = meta.get("aggregate_file_hashes")
    if not isinstance(hashes, dict) or not hashes or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in hashes.items()
    ):
        # Every legitimately built or migrated generation records at least
        # stats.json and the per-language aggregate files, so an empty table
        # is corruption, not a zero-data state.
        raise RuntimeError("Live generation meta.json has no valid aggregate_file_hashes table.")
    return hashes


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
