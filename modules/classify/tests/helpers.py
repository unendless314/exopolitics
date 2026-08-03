"""Shared test helpers for the classify test suite.

Helpers here stay explicit on purpose: callers pass queue status, queue
reason, provider capability, and response content directly, so every test
states its own preconditions instead of inheriting hidden fixture state.

`create_mock_ingest_tables()` is a manually maintained copy of the upstream
ingest schema. It exists only for isolated classify unit tests; the real
handoff contract is pinned by test_ingest_handoff.py, which applies the
actual ingest migrations.
"""

import contextlib
import json
import pathlib
import tempfile
from typing import Any, Dict, Iterator, Optional

import httpx

from modules.classify.src.config import (
    ClassifyConfig,
    ExecutionPolicy,
    ModelSettingsYaml,
    PromptTemplatesYaml,
    ProviderConfig,
    RequestDefaults,
    TemplateConfig,
)
from modules.classify.src.database import get_connection, run_migrations

CLASSIFY_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"
INGEST_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "ingest" / "src" / "migrations"

TEST_REQUEST_URL = "https://api.test.com/v1/chat/completions"


def create_mock_ingest_tables(db_path: pathlib.Path) -> None:
    """Seeds the minimal upstream ingest schema for isolated unit tests."""
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
                ingest_dedup_key TEXT NOT NULL UNIQUE,
                dedup_rule TEXT NOT NULL,
                ingest_status TEXT NOT NULL CHECK (ingest_status IN ('ingested'))
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item_text (
                source_item_text_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                sanitized_text TEXT NOT NULL,
                sanitization_method TEXT NOT NULL,
                html_detected INTEGER NOT NULL CHECK (html_detected IN (0, 1)),
                was_truncated INTEGER NOT NULL CHECK (was_truncated IN (0, 1)),
                text_processing_status TEXT NOT NULL CHECK (text_processing_status IN ('completed', 'low_context', 'failed')),
                text_processing_reason TEXT,
                raw_text_length INTEGER,
                sanitized_text_length INTEGER NOT NULL,
                reduction_ratio REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE RESTRICT
            );
        """)
        conn.commit()
    finally:
        conn.close()


@contextlib.contextmanager
def temp_classify_db(mock_ingest_schema: bool = True) -> Iterator[pathlib.Path]:
    """Yields a temporary DB path with classify migrations applied.

    When mock_ingest_schema is True, the manual upstream mock schema is
    created first so classification_result's FK target exists.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = pathlib.Path(tmp) / "canonical.db"
        if mock_ingest_schema:
            create_mock_ingest_tables(db_path)
        run_migrations(db_path, CLASSIFY_MIGRATIONS_DIR)
        yield db_path


def seed_source_item(
    db_path: pathlib.Path,
    item_id: int,
    title: str,
    text: str,
    status: str = "completed",
    reason: Optional[str] = None,
) -> None:
    """Seeds one upstream source_item + source_item_text pair.

    status/reason map to source_item_text.text_processing_status and
    text_processing_reason; the row is always ingest_status='ingested'.
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO source_item (
                source_item_id, source_id, title, ingest_dedup_key, dedup_rule, ingest_status, fetched_at
            ) VALUES (?, 1, ?, ?, 'guid', 'ingested', '2026-06-13T21:00:00Z')
        """, (item_id, title, f"key-{item_id}"))

        cursor.execute("""
            INSERT INTO source_item_text (
                source_item_id, sanitized_text, sanitization_method, html_detected, was_truncated,
                text_processing_status, text_processing_reason, sanitized_text_length, created_at, updated_at
            ) VALUES (?, ?, 'clean_v1', 0, 0, ?, ?, ?, '2026-06-13T21:00:00Z', '2026-06-13T21:00:00Z')
        """, (item_id, text, status, reason, len(text)))
        conn.commit()
    finally:
        conn.close()


def valid_llm_response(**overrides: Any) -> Dict[str, Any]:
    """Returns a canonical valid model output dict, with optional overrides."""
    response: Dict[str, Any] = {
        "topic_class": "core",
        "classification_confidence": 0.9,
        "classification_reason": "Direct reference to declassified sensor video.",
        "content_density": "high",
        "source_text_quality": "strong",
        "primary_language_code": "en",
        "governmental_involvement": 1,
        "content_timeliness": "current",
        "primary_evidence_type": "radar_sensor",
    }
    response.update(overrides)
    return response


def make_http_response(status_code: int, body: Optional[Dict[str, Any]] = None) -> httpx.Response:
    """Builds a real httpx.Response so raise_for_status behaves like production."""
    request = httpx.Request("POST", TEST_REQUEST_URL)
    return httpx.Response(status_code, json=body if body is not None else {}, request=request)


def make_completion_response(model_output: Dict[str, Any]) -> httpx.Response:
    """Builds a real 200 chat-completion response wrapping the model output."""
    return make_http_response(200, {
        "choices": [{"message": {"content": json.dumps(model_output)}}]
    })


def make_config(
    *,
    supports_structured_output: bool = False,
    model_name: str = "gpt-5.4-mini",
    api_base: str = "https://api.test.com/v1",
    api_key_env: str = "TEST_API_KEY",
    temperature: float = 0.1,
    top_p: Optional[float] = 0.95,
    max_output_tokens: int = 1024,
    batch_size: int = 20,
    max_concurrent_requests: int = 3,
    rate_limit_per_minute: int = 60,
    request_timeout_seconds: float = 45.0,
    retry_attempts: int = 3,
    backoff_factor: float = 2.0,
    system_instruction: str = "You are a classifier.",
    user_prompt_template: str = "Title: {title}, Text: {sanitized_text}",
) -> ClassifyConfig:
    """Builds a real ClassifyConfig with explicit, test-controlled values."""
    settings = ModelSettingsYaml(
        active_provider="test-provider",
        active_prompt_template="test_template",
        request_defaults=RequestDefaults(
            temperature=temperature,
            top_p=top_p,
            max_output_tokens=max_output_tokens,
        ),
        execution_policy=ExecutionPolicy(
            batch_size=batch_size,
            max_concurrent_requests=max_concurrent_requests,
            rate_limit_per_minute=rate_limit_per_minute,
            request_timeout_seconds=request_timeout_seconds,
            retry_attempts=retry_attempts,
            backoff_factor=backoff_factor,
        ),
        providers={
            "test-provider": ProviderConfig(
                api_type="openai_compatible",
                api_key_env=api_key_env,
                model_name=model_name,
                supports_structured_output=supports_structured_output,
                api_base=api_base,
            )
        },
    )
    templates = PromptTemplatesYaml(
        templates={
            "test_template": TemplateConfig(
                version="v4.0",
                system_instruction=system_instruction,
                user_prompt_template=user_prompt_template,
            )
        }
    )
    return ClassifyConfig(settings, templates)
