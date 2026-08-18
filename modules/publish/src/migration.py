"""
One-time migration from the pre-B1 flat export layout to generations.

Part of Phase B1 of
known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md. When no
``current.json`` exists but a flat ``stats.json`` does, the flat tree is a
legacy pre-pointer export. Because the old runner could stop between its DB
commits and its per-file promotion, the flat tree is never trusted on the
DB's say-so: every planned artifact's bytes are verified against the flat
files (stats.json compared as dicts without its run wall-clock timestamp),
and the flat ``*.json`` set under the configured language directories must
equal the plan's artifact set. Only a fully matching tree is moved into
``generations/``; anything else falls back to building the first complete
generation from the DB plan.

Ownership boundary: only the configured language directories and stats.json
are moved. Any other top-level entries (e.g. residual non-configured
language directories, ``assets/``) stay at the export root untouched.
"""
import hashlib
import json
import logging
import os
import pathlib
from typing import Any, Dict

from . import generation_store
from .config import PublishConfig
from .database import PublishRepository
from .generation import (
    GenerationPlan,
    iter_planned_artifact_bytes,
    serialize_json_bytes,
)

logger = logging.getLogger("publish.migration")

_STATS_TIMESTAMP_KEY = "last_export_run_timestamp"


def flat_layout_present(export_dir: pathlib.Path) -> bool:
    """A flat (pre-B1) export tree is recognized by its root stats.json."""
    return (export_dir / "stats.json").is_file()


def verify_flat_tree(
    export_dir: pathlib.Path,
    plan: GenerationPlan,
    repo: PublishRepository,
    config: PublishConfig,
) -> bool:
    """
    True only when the flat tree is byte-exact with the deterministic plan
    built from the current DB snapshot: every planned artifact exists with
    identical bytes (stats.json compared as parsed dicts without
    ``last_export_run_timestamp``), and the flat side holds no extra
    ``*.json`` files under the configured language directories.
    """
    expected: Dict[str, bytes] = {}
    for rel_path, body in iter_planned_artifact_bytes(plan, repo, config):
        expected[rel_path] = body

    for rel_path, body in expected.items():
        if rel_path == "stats.json":
            continue
        flat_file = export_dir / rel_path
        if not flat_file.is_file() or flat_file.read_bytes() != body:
            return False

    try:
        flat_stats = json.loads((export_dir / "stats.json").read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(flat_stats, dict):
        return False
    # The migrated generation id and the pointer's export_completed_at both
    # derive from the flat stats' run timestamp, so a tree without a valid
    # one cannot be migrated: treat it as a verification failure and let the
    # caller fall back to a bootstrap build (plan: "invalid timestamp counts
    # as verification failure").
    if not generation_store.is_valid_iso_timestamp(flat_stats.get(_STATS_TIMESTAMP_KEY)):
        return False
    flat_comparable = {k: v for k, v in flat_stats.items() if k != _STATS_TIMESTAMP_KEY}
    planned_comparable = {k: v for k, v in plan.stats.items() if k != _STATS_TIMESTAMP_KEY}
    if flat_comparable != planned_comparable:
        return False

    flat_rel_paths = {"stats.json"}
    for lang in config.target_languages:
        lang_dir = export_dir / lang
        if lang_dir.is_dir():
            for path in lang_dir.rglob("*.json"):
                flat_rel_paths.add(path.relative_to(export_dir).as_posix())
    return flat_rel_paths == set(expected.keys())


def migrate_flat_tree(
    export_dir: pathlib.Path,
    config: PublishConfig,
    content_fingerprint: str,
    run_ts: str,
) -> Dict[str, Any]:
    """
    Move a verified flat tree into ``generations/<id>`` and return the
    pointer dict for the caller to switch atomically. The generation id
    derives from the flat stats' ``last_export_run_timestamp``; meta.json
    hashes are computed from the actual moved bytes. Only publish-owned
    artifacts move: configured language directories and stats.json.
    """
    flat_stats = json.loads((export_dir / "stats.json").read_bytes().decode("utf-8"))
    completed_at = flat_stats.get(_STATS_TIMESTAMP_KEY)
    if not generation_store.is_valid_iso_timestamp(completed_at):
        raise RuntimeError(
            f"Flat stats.json has no valid {_STATS_TIMESTAMP_KEY}: {completed_at!r}"
        )
    generations_dir = export_dir / generation_store.GENERATIONS_DIR_NAME
    generation = generation_store.allocate_generation_id(generations_dir, completed_at)
    generation_root = generation_store.generation_root_for(export_dir, generation)
    generation_root.mkdir(parents=True)

    for lang in config.target_languages:
        source = export_dir / lang
        if source.is_dir():
            os.rename(source, generation_root / lang)
    os.rename(export_dir / "stats.json", generation_root / "stats.json")

    aggregate_file_hashes: Dict[str, str] = {}
    for path in sorted(generation_root.rglob("*.json")):
        rel_path = path.relative_to(generation_root).as_posix()
        if "/items/" in rel_path:
            continue
        aggregate_file_hashes[rel_path] = (
            f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        )

    meta = {
        "generation": generation,
        "created_at": run_ts,
        "content_fingerprint": content_fingerprint,
        "aggregate_file_hashes": aggregate_file_hashes,
    }
    with open(generation_root / "meta.json", "wb") as f:
        f.write(serialize_json_bytes(meta))

    logger.info(f"Migrated flat export tree into generation '{generation}'.")
    return {
        "generation": generation,
        "export_completed_at": completed_at,
        "last_successful_run_at": run_ts,
        "languages": list(config.target_languages.keys()),
        "content_fingerprint": content_fingerprint,
    }
