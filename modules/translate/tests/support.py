"""
Shared test support for the translate test suite.

Introduced by Phase 0 of known_issues/TRANSLATE_TEST_MAINTAINABILITY_PLAN.md.

Contract of this module (plan Phase 0 items 5-7):

- Key preconditions are explicit. Bullet shapes, translation status,
  retry_count, target languages and curation downstream_action are required
  arguments; helpers must not hide them behind defaults.
- The minimal upstream tables created here are an ISOLATED-TEST-ONLY fixture.
  They contain only the columns the handoff assembler queries. They are NOT a
  representation of the canonical ingest/classify/curate schemas and must not
  be treated as one (plan section 3.6).
- Nothing here imports test helpers from other modules, reads the workspace
  canonical DB (data/canonical.db), or reads .env files.
"""

import json
import pathlib
import sqlite3
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import httpx
import yaml

from modules.translate.src.config import TranslateConfig, validate_and_load_config
from modules.translate.src.database import get_connection, run_migrations

DEFAULT_TRANSLATE_MIGRATIONS = (
    pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"
)
ACTIVE_CONFIG_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "config"
)


# ---------------------------------------------------------------------------
# Temporary workspace / database builders
# ---------------------------------------------------------------------------

def create_minimal_upstream_tables(db_path: pathlib.Path) -> None:
    """Creates the minimal upstream fixture the assembler reads.

    ISOLATED-TEST-ONLY: only the columns consumed by
    approved_content_record.assemble_approved_content_records() are present.
    This fixture is not a canonical schema contract for ingest/curate.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item (
                source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                canonical_url TEXT,
                ingest_status TEXT NOT NULL CHECK (ingest_status IN ('ingested', 'draft'))
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_decision (
                curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                curate_status TEXT NOT NULL CHECK (curate_status IN ('approved', 'rejected', 'failed')),
                downstream_action TEXT CHECK (downstream_action IS NULL OR downstream_action IN ('publish_link', 'publish_summary', 'edit_rewrite', 'reject_discard')),
                decision_reason TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                curated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS curation_output (
                curation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                display_title TEXT NOT NULL,
                summary_short TEXT NOT NULL,
                bullet_1 TEXT,
                bullet_2 TEXT,
                bullet_3 TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


def build_test_db(
    db_path: pathlib.Path,
    migrations_dir: pathlib.Path = DEFAULT_TRANSLATE_MIGRATIONS,
) -> None:
    """Builds a temporary canonical DB: minimal upstream fixture plus the real
    translate migrations (approved_content_record / translation_output)."""
    create_minimal_upstream_tables(db_path)
    run_migrations(db_path, migrations_dir)


def build_temp_workspace(
    workspace_root: pathlib.Path,
    migrations_dir: pathlib.Path = DEFAULT_TRANSLATE_MIGRATIONS,
) -> pathlib.Path:
    """Creates a temporary workspace layout ``<workspace_root>/data/canonical.db``
    with the test DB built, and returns the db_path.

    The orchestrator derives the process lock path as
    ``db_path.parent.parent / 'data' / 'translate_runner.lock'``, so keeping the
    DB under ``<workspace_root>/data/`` guarantees the lock file also stays
    inside the temporary workspace.
    """
    db_path = workspace_root / "data" / "canonical.db"
    build_test_db(db_path, migrations_dir)
    return db_path


# ---------------------------------------------------------------------------
# Active config loader
# ---------------------------------------------------------------------------

def load_active_config(config_dir: pathlib.Path = ACTIVE_CONFIG_DIR) -> TranslateConfig:
    """Loads and validates the real active translate config.

    Use this instead of handwritten MagicMock configs whenever a test needs
    the effective runtime policy values (plan sections 3.2, 3.4).
    """
    return validate_and_load_config(config_dir)


def write_config_dir(
    config_dir: pathlib.Path,
    *,
    content_ratio_limit: float,
    supports_structured_output: bool,
    top_p: Optional[float] = 0.95,
    target_languages: Optional[Dict[str, Dict[str, Any]]] = None,
    retry_attempts: int = 3,
    backoff_factor: float = 0.1,
    batch_size: int = 20,
    max_concurrent_requests: int = 3,
    rate_limit_per_minute: int = 60,
    request_timeout_seconds: float = 10.0,
    model_name: str = "gpt-5.4-mini",
    prompt_version: str = "translator_v2",
    api_key_env: str = "TEST_TRANSLATE_API_KEY",
) -> pathlib.Path:
    """Writes a complete temporary config dir (model_settings.yaml +
    prompt_templates.yaml) and returns its path.

    The effective ``content_ratio_limit`` and the provider capability are
    explicit arguments (plan Phase 1): tests derive limits from the config
    object loaded via validate_and_load_config(), never from handwritten
    values passed around as "runtime" behavior.
    """
    if target_languages is None:
        target_languages = {"zh": {"label": "Traditional Chinese", "max_title_length": 120}}

    settings = {
        "active_provider": "test-provider",
        "active_prompt_template": prompt_version,
        "request_defaults": {
            "temperature": 0.3,
            "top_p": top_p,
            "max_output_tokens": 4096,
        },
        "target_languages": target_languages,
        "execution_policy": {
            "batch_size": batch_size,
            "max_concurrent_requests": max_concurrent_requests,
            "rate_limit_per_minute": rate_limit_per_minute,
            "request_timeout_seconds": request_timeout_seconds,
            "retry_attempts": retry_attempts,
            "backoff_factor": backoff_factor,
        },
        "validation": {
            "default_max_title_length": 500,
            "content_ratio_limit": content_ratio_limit,
        },
        "providers": {
            "test-provider": {
                "api_type": "openai_compatible",
                "api_base": "https://api.test.invalid",
                "api_key_env": api_key_env,
                "model_name": model_name,
                "supports_structured_output": supports_structured_output,
            }
        },
    }
    templates = {
        "templates": {
            prompt_version: {
                "version": prompt_version,
                "description": "temporary test template",
                "system_instruction": "System Instruction",
                "user_prompt_template": (
                    "Target Lang: {target_language}\n"
                    "Title: {display_title}\n"
                    "Summary: {summary_short}\n"
                    "B1: {bullet_1}\nB2: {bullet_2}\nB3: {bullet_3}"
                ),
            }
        }
    }

    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "model_settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8"
    )
    (config_dir / "prompt_templates.yaml").write_text(
        yaml.safe_dump(templates, allow_unicode=True), encoding="utf-8"
    )
    return config_dir


# ---------------------------------------------------------------------------
# Response factories
# ---------------------------------------------------------------------------

def make_five_field_response(
    *,
    title: Any,
    summary: Any,
    bullet_1: Any,
    bullet_2: Any,
    bullet_3: Any,
) -> Dict[str, Any]:
    """Builds a translator_v2 five-key mock LLM response payload.

    Bullet shape is an explicit precondition: pass None or content per slot.
    """
    return {
        "translated_title": title,
        "translated_summary": summary,
        "translated_bullet_1": bullet_1,
        "translated_bullet_2": bullet_2,
        "translated_bullet_3": bullet_3,
    }


def make_chat_completion_body(response_content: Any) -> Dict[str, Any]:
    """Wraps a five-field response dict in the chat.completions envelope that
    _parse_response_content() consumes. ``response_content`` is serialized as
    the message content string; pass it explicitly."""
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(response_content)
                    if not isinstance(response_content, str)
                    else response_content
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# Seed helpers (explicit bullet shape / status / retry_count)
# ---------------------------------------------------------------------------

def seed_curation_approval(
    conn: sqlite3.Connection,
    *,
    source_item_id: int,
    downstream_action: str,
    display_title: str,
    summary_short: str,
    bullet_1: Optional[str],
    bullet_2: Optional[str],
    bullet_3: Optional[str],
    curated_at: str = "2026-06-20T12:00:00Z",
    updated_at: str = "2026-06-20T12:00:00Z",
) -> None:
    """Seeds source_item + approved curation_decision + curation_output.

    ``downstream_action`` and the three bullet slots are explicit so tests can
    construct both valid shapes and the illegal combinations the assembler
    must reject (plan section 3.1).
    """
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO source_item (source_item_id, source_id, title, ingest_status) "
        "VALUES (?, 1, ?, 'ingested')",
        (source_item_id, display_title),
    )
    cursor.execute(
        "INSERT INTO curation_decision (source_item_id, curate_status, downstream_action, "
        "model_name, prompt_version, curated_at, created_at) "
        "VALUES (?, 'approved', ?, 'curator', 'v1', ?, ?)",
        (source_item_id, downstream_action, curated_at, curated_at),
    )
    cursor.execute(
        "INSERT INTO curation_output (source_item_id, display_title, summary_short, "
        "bullet_1, bullet_2, bullet_3, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_item_id, display_title, summary_short,
            bullet_1, bullet_2, bullet_3, curated_at, updated_at,
        ),
    )
    conn.commit()


def seed_approved_record(
    conn: sqlite3.Connection,
    *,
    parent_content_id: int,
    source_item_id: int,
    display_title: str,
    summary_short: str,
    bullet_1: Optional[str],
    bullet_2: Optional[str],
    bullet_3: Optional[str],
    content_fingerprint: str,
    content_language_code: str,
    approved_at: str = "2026-06-20T12:00:00Z",
) -> None:
    """Seeds source_item + an approved_content_record handoff row directly."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO source_item (source_item_id, source_id, title, ingest_status) "
        "VALUES (?, 1, ?, 'ingested')",
        (source_item_id, display_title),
    )
    cursor.execute(
        "INSERT INTO approved_content_record (parent_content_id, source_item_id, "
        "display_title, summary_short, bullet_1, bullet_2, bullet_3, content_fingerprint, "
        "content_language_code, approved_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            parent_content_id, source_item_id, display_title, summary_short,
            bullet_1, bullet_2, bullet_3, content_fingerprint,
            content_language_code, approved_at, approved_at, approved_at,
        ),
    )
    conn.commit()


def seed_translation_row(
    conn: sqlite3.Connection,
    *,
    parent_content_id: int,
    source_item_id: int,
    language_code: str,
    display_title: Optional[str],
    summary_short: Optional[str],
    bullet_1: Optional[str],
    bullet_2: Optional[str],
    bullet_3: Optional[str],
    source_fingerprint: str,
    status: str,
    retry_count: int,
    model_name: str = "gpt-5.4-mini",
    prompt_version: str = "translator_v2",
    translated_at: Optional[str] = "2026-06-20T12:00:00Z",
) -> None:
    """Seeds a translation_output row. ``status`` and ``retry_count`` are
    explicit preconditions (queue eligibility depends on both)."""
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO translation_output (parent_content_id, source_item_id, language_code, "
        "display_title, summary_short, bullet_1, bullet_2, bullet_3, source_fingerprint, "
        "translation_status, retry_count, model_name, prompt_version, translated_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-06-20T12:00:00Z')",
        (
            parent_content_id, source_item_id, language_code, display_title, summary_short,
            bullet_1, bullet_2, bullet_3, source_fingerprint, status, retry_count,
            model_name, prompt_version, translated_at,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Row snapshots
# ---------------------------------------------------------------------------

def snapshot_translation_row(
    conn: sqlite3.Connection,
    *,
    parent_content_id: int,
    language_code: str,
) -> Optional[Dict[str, Any]]:
    """Returns the full translation_output row as a plain dict, or None.

    Snapshots let tests assert "row unchanged" by comparing whole dicts
    instead of re-deriving individual columns.
    """
    row = conn.execute(
        "SELECT * FROM translation_output WHERE parent_content_id = ? AND language_code = ?",
        (parent_content_id, language_code),
    ).fetchone()
    return dict(row) if row is not None else None


def snapshot_approved_record(
    conn: sqlite3.Connection,
    *,
    source_item_id: int,
) -> Optional[Dict[str, Any]]:
    """Returns the full approved_content_record row as a plain dict, or None."""
    row = conn.execute(
        "SELECT * FROM approved_content_record WHERE source_item_id = ?",
        (source_item_id,),
    ).fetchone()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Fake LLM HTTP client (no real network, no GC-dependent lifecycle)
# ---------------------------------------------------------------------------

def make_http_response(
    status_code: int,
    body: Optional[Any] = None,
    raw_content: Optional[bytes] = None,
) -> httpx.Response:
    """Builds a real httpx.Response so status handling, .json() and
    raise_for_status() behave exactly as in production."""
    request = httpx.Request("POST", "https://api.test.invalid/chat/completions")
    if raw_content is not None:
        content = raw_content
    elif body is not None:
        content = json.dumps(body).encode("utf-8")
    else:
        content = b""
    return httpx.Response(status_code, content=content, request=request)


class FakeLLMClient:
    """Test-controlled stand-in for httpx.AsyncClient.

    Records every request and replays a script of queued responses and
    exceptions in order. No real network, no connection pool, no resource
    lifecycle dependent on garbage collection.
    """

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self._script: List[Any] = []

    def queue_response(
        self,
        status_code: int,
        body: Optional[Any] = None,
        raw_content: Optional[bytes] = None,
    ) -> None:
        self._script.append(make_http_response(status_code, body, raw_content))

    def queue_exception(self, exc: Exception) -> None:
        self._script.append(exc)

    async def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        if not self._script:
            raise AssertionError(
                "FakeLLMClient script exhausted: the code under test issued "
                "more requests than were scripted"
            )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------------------
# Mock config builder (key capabilities are explicit arguments)
# ---------------------------------------------------------------------------

def make_target_language(*, label: str, max_title_length: int) -> Any:
    """Builds one TargetLanguageConfig stand-in."""
    return SimpleNamespace(label=label, max_title_length=max_title_length)


def build_mock_config(
    *,
    target_languages: Dict[str, Any],
    supports_structured_output: bool,
    content_ratio_limit: float,
    top_p: Optional[float] = 0.95,
    retry_attempts: int = 3,
    backoff_factor: float = 0.1,
    batch_size: int = 20,
    max_concurrent_requests: int = 3,
    rate_limit_per_minute: int = 60,
    request_timeout_seconds: float = 10.0,
    model_name: str = "gpt-5.4-mini",
    prompt_version: str = "translator_v2",
    api_key_env: str = "TEST_TRANSLATE_API_KEY",
) -> MagicMock:
    """Builds a TranslateConfig MagicMock for isolated execution tests.

    Provider capability (``supports_structured_output``), the target language
    set and the effective ``content_ratio_limit`` are explicit arguments so a
    test cannot silently inherit production-looking values. Tests that need
    the real runtime policy should use load_active_config() or a temporary
    YAML config dir instead of this mock.
    """
    config = MagicMock(spec=TranslateConfig)
    config.active_provider_name = "test-provider"
    config.active_provider = MagicMock()
    config.active_provider.model_name = model_name
    config.active_provider.api_base = "https://api.test.invalid"
    config.active_provider.api_key_env = api_key_env
    config.active_provider.supports_structured_output = supports_structured_output

    config.active_template = MagicMock()
    config.active_template.version = prompt_version
    config.active_template.system_instruction = "System Instruction"
    config.active_template.user_prompt_template = (
        "Target Lang: {target_language}\n"
        "Title: {display_title}\n"
        "Summary: {summary_short}\n"
        "B1: {bullet_1}\nB2: {bullet_2}\nB3: {bullet_3}"
    )

    config.execution_policy = MagicMock()
    config.execution_policy.batch_size = batch_size
    config.execution_policy.max_concurrent_requests = max_concurrent_requests
    config.execution_policy.rate_limit_per_minute = rate_limit_per_minute
    config.execution_policy.request_timeout_seconds = request_timeout_seconds
    config.execution_policy.retry_attempts = retry_attempts
    config.execution_policy.backoff_factor = backoff_factor

    config.request_defaults = MagicMock()
    config.request_defaults.temperature = 0.3
    config.request_defaults.top_p = top_p
    config.request_defaults.max_output_tokens = 4096

    config.target_languages = target_languages

    config.validation = MagicMock()
    config.validation.default_max_title_length = 500
    config.validation.content_ratio_limit = content_ratio_limit
    return config
