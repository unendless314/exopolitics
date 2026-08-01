"""
Shared test support for the publish module test suite.

This module is publish-owned and imported explicitly by tests under
``modules/publish/tests/`` (no ``conftest.py`` implicit fixtures). It
provides:

- ``create_upstream_tables``: a minimal five-column upstream schema that
  mirrors only the publish read dependencies documented in
  ``docs/DATA_CONTRACT.md`` section 4. It is NOT the canonical upstream
  contract; the real-migration handoff contract test
  (``test_handoff_contract.py``) applies the active upstream migrations
  instead.
- ``make_config``: a config factory with overridable target languages,
  batch size and latest limit.
- ``seed_item``: an explicit seed helper. Callers state the upstream
  conditions each scenario depends on (per-language display title,
  summary/bullets, translation status and fingerprint, curate status,
  downstream action, author metadata); special one-off rows stay in the
  individual test cases.
- artifact readers for the exported JSON tree.
- ``FakeClock``: a deterministic clock patching the publish-owned UTC
  clock in both the ``database`` and ``orchestrator`` namespaces, so
  timestamp-sensitive assertions never depend on wall time or same-second
  races.

Test-only helpers must never be imported from production modules, and this
module must not become a runtime dependency.
"""

import asyncio
import contextlib
import datetime
import json
import pathlib
from typing import Any, Dict, Iterator, Optional, Tuple
from unittest.mock import patch

from modules.publish.src.config import (
    PublishConfig,
    PublishSettingsYaml,
    ExecutionPolicy,
    IndexPolicy,
)
from modules.publish.src.database import get_connection
from modules.publish.src.orchestrator import orchestrate_run

PUBLISH_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

DEFAULT_TARGET_LANGUAGES = {"zh": "Traditional Chinese", "en": "English"}
DEFAULT_AUTHOR_METADATA = '{"source_module": "edit", "writer_type": "human", "editor": "john_doe"}'
DEFAULT_APPROVED_AT = "2026-06-20T12:00:00Z"
DEFAULT_FETCHED_AT = "2026-06-20T10:00:00Z"

_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def create_upstream_tables(db_path: pathlib.Path) -> None:
    """
    Create the minimal publish-test upstream schema: the current five-column
    structured-content shape where approved_content_record and
    translation_output carry display_title/summary_short/bullet_1..3.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item (
                source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                source_item_guid TEXT,
                canonical_url TEXT,
                title TEXT NOT NULL,
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                ingest_dedup_key TEXT NOT NULL,
                dedup_rule TEXT NOT NULL,
                ingest_status TEXT NOT NULL
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approved_content_record (
                parent_content_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                display_title TEXT NOT NULL,
                summary_short TEXT NOT NULL,
                bullet_1 TEXT,
                bullet_2 TEXT,
                bullet_3 TEXT,
                content_fingerprint TEXT NOT NULL,
                content_language_code TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                author_metadata TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS translation_output (
                translation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_content_id INTEGER NOT NULL,
                source_item_id INTEGER NOT NULL,
                language_code TEXT NOT NULL,
                display_title TEXT,
                summary_short TEXT,
                bullet_1 TEXT,
                bullet_2 TEXT,
                bullet_3 TEXT,
                source_fingerprint TEXT NOT NULL,
                translation_status TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                translated_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (parent_content_id) REFERENCES approved_content_record (parent_content_id) ON DELETE CASCADE,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id),
                UNIQUE (parent_content_id, language_code)
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_decision (
                curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                curate_status TEXT NOT NULL,
                downstream_action TEXT,
                decision_reason TEXT,
                decision_actor TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                curated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


def make_config(
    *,
    target_languages: Optional[Dict[str, str]] = None,
    coverage_policy: str = "strict_match",
    export_dir: Optional[pathlib.Path] = None,
    batch_size: int = 10,
    latest_limit: int = 5,
    archive_granularity: str = "month",
) -> PublishConfig:
    """Build a PublishConfig with overridable languages and bounded limits."""
    settings = PublishSettingsYaml(
        target_languages=target_languages if target_languages is not None else dict(DEFAULT_TARGET_LANGUAGES),
        coverage_policy=coverage_policy,
        execution_policy=ExecutionPolicy(
            default_export_dir=str(export_dir) if export_dir is not None else "data/publish_export",
            batch_size=batch_size,
        ),
        index_policy=IndexPolicy(latest_limit=latest_limit, archive_granularity=archive_granularity),
    )
    return PublishConfig(settings)


def seed_item(
    db_path: pathlib.Path,
    item_id: int,
    title: str,
    published_at: str,
    *,
    curate_status: str = "approved",
    downstream_action: str = "publish_summary",
    author_metadata: Optional[str] = DEFAULT_AUTHOR_METADATA,
    content_fingerprint: str = "fp_123",
    approved_at: str = DEFAULT_APPROVED_AT,
    translations: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """
    Seed one source item with its approved record, curation decision and one
    translation row per entry in ``translations``.

    ``translations`` maps language_code to an override dict with keys:

    - ``status``: translation_status (default ``"completed"``). A
      non-completed row stores NULL summary/bullets content fields.
    - ``fingerprint``: source_fingerprint (default ``content_fingerprint``).
    - ``display_title``: default ``"EN {title}"`` for ``en`` else ``title``.
    - ``summary``: default ``"{lang} summary for {title}"``.
    - ``bullets``: 3-tuple for the structured content columns. Defaults to a
      per-language 3-tuple for ``publish_summary`` and ``None`` for
      ``publish_link``. Explicit ``None`` stores NULL bullets on a completed
      row.
    - ``translated_at``: default ``approved_at``.

    When ``translations`` is None, seeds completed ``zh`` and ``en`` rows.

    Note: rows are written with ``INSERT OR REPLACE``, which deletes and
    re-inserts on conflict; re-seeding an item that was already published
    therefore cascade-deletes its ``publish_record`` and resets all
    publish-layer state. Scenarios that mutate upstream state after a
    publish run must use targeted ``UPDATE``/``INSERT`` statements instead.
    """
    if translations is None:
        translations = {"zh": {}, "en": {}}

    resolved: Dict[str, Dict[str, Any]] = {}
    for lang, overrides in translations.items():
        status = overrides.get("status", "completed")
        if "bullets" in overrides:
            bullets = overrides["bullets"]
        else:
            bullets = None if downstream_action == "publish_link" else (
                f"{lang} key claim for {title}",
                f"{lang} evidence level for {title}",
                f"{lang} objective impact for {title}",
            )
        resolved[lang] = {
            "status": status,
            "fingerprint": overrides.get("fingerprint", content_fingerprint),
            "display_title": overrides.get(
                "display_title", f"EN {title}" if lang == "en" else title
            ),
            "summary": overrides.get("summary", f"{lang} summary for {title}"),
            "bullets": bullets,
            "translated_at": overrides.get("translated_at", approved_at),
        }

    first_lang = next(iter(resolved))
    first = resolved[first_lang]

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO source_item (source_item_id, source_id, title, canonical_url, published_at, fetched_at, ingest_dedup_key, dedup_rule, ingest_status)
            VALUES (?, 1, ?, ?, ?, ?, ?, 'guid', 'ingested')
        """, (item_id, title, f"https://example.com/{item_id}", published_at, DEFAULT_FETCHED_AT, f"key_{item_id}"))

        cursor.execute("""
            INSERT OR REPLACE INTO approved_content_record (
                parent_content_id, source_item_id, display_title, summary_short,
                bullet_1, bullet_2, bullet_3,
                content_fingerprint, content_language_code, approved_at,
                author_metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'zh', ?, ?, ?, ?)
        """, (
            item_id * 10,
            item_id,
            title,
            first["summary"],
            first["bullets"][0] if first["bullets"] else None,
            first["bullets"][1] if first["bullets"] else None,
            first["bullets"][2] if first["bullets"] else None,
            content_fingerprint,
            approved_at,
            author_metadata,
            approved_at,
            approved_at,
        ))

        cursor.execute("""
            INSERT OR REPLACE INTO curation_decision (source_item_id, curate_status, downstream_action, decision_reason, decision_actor, model_name, prompt_version, curated_at, created_at, updated_at)
            VALUES (?, ?, ?, 'Approved', 'operator', 'curator', 'v1', ?, ?, ?)
        """, (item_id, curate_status, downstream_action, approved_at, approved_at, approved_at))

        for index, (lang, row) in enumerate(resolved.items()):
            if row["status"] == "completed":
                summary = row["summary"]
                bullets: Optional[Tuple[str, str, str]] = row["bullets"]
            else:
                # Non-completed rows carry NULL content fields (summary and
                # bullets); display_title is preserved so a later recovery to
                # 'completed' only needs to restore the content fields.
                summary = None
                bullets = None
            cursor.execute("""
                INSERT OR REPLACE INTO translation_output (
                    translation_output_id, parent_content_id, source_item_id, language_code,
                    display_title, summary_short, bullet_1, bullet_2, bullet_3,
                    source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'translator', 'v1', ?, ?)
            """, (
                item_id * 100 + index,
                item_id * 10,
                item_id,
                lang,
                row["display_title"],
                summary,
                bullets[0] if bullets else None,
                bullets[1] if bullets else None,
                bullets[2] if bullets else None,
                row["fingerprint"],
                row["status"],
                row["translated_at"],
                row["translated_at"],
            ))

        conn.commit()
    finally:
        conn.close()


def run_publish(config: PublishConfig, db_path: pathlib.Path, export_dir: pathlib.Path, rebuild: bool = False) -> Dict[str, Any]:
    """Run the orchestrator synchronously and return its summary."""
    return asyncio.run(orchestrate_run(config, db_path, export_dir, rebuild=rebuild))


def read_json(path: pathlib.Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_item(export_dir: pathlib.Path, lang: str, slug: str) -> Dict[str, Any]:
    return read_json(export_dir / lang / "items" / f"{slug}.json")


def read_index(export_dir: pathlib.Path, lang: str) -> Any:
    return read_json(export_dir / lang / "index.json")


def read_archive(export_dir: pathlib.Path, lang: str, month: str) -> Any:
    return read_json(export_dir / lang / "archives" / f"archive_{month.replace('-', '_')}.json")


def read_manifest(export_dir: pathlib.Path, lang: str) -> Any:
    return read_json(export_dir / lang / "archives" / "index.json")


def read_stats(export_dir: pathlib.Path) -> Dict[str, Any]:
    return read_json(export_dir / "stats.json")


class FakeClock:
    """
    Deterministic UTC clock for timestamp-sensitive publish tests.

    ``patch()`` replaces the publish-owned clock in both the ``database``
    and ``orchestrator`` namespaces, so every timestamp the run writes
    (publish_record, publish_language_status, archive metadata, stats)
    comes from this clock. Advance or set it between runs to assert exact
    values without wall-time or same-second races.
    """

    def __init__(self, start: str = "2026-07-01T00:00:00Z") -> None:
        self._current = self._parse(start)

    @staticmethod
    def _parse(value: str) -> datetime.datetime:
        return datetime.datetime.strptime(value, _ISO_FORMAT).replace(tzinfo=datetime.timezone.utc)

    @property
    def now_iso(self) -> str:
        return self._current.strftime(_ISO_FORMAT)

    def set(self, value: str) -> None:
        self._current = self._parse(value)

    def advance(self, **kwargs: int) -> None:
        """Advance the clock by a datetime.timedelta(**kwargs)."""
        self._current += datetime.timedelta(**kwargs)

    @contextlib.contextmanager
    def patch(self) -> Iterator["FakeClock"]:
        with patch(
            "modules.publish.src.orchestrator.get_utc_now_iso8601",
            new=lambda: self.now_iso,
        ), patch(
            "modules.publish.src.database.get_utc_now_iso8601",
            new=lambda: self.now_iso,
        ):
            yield self
