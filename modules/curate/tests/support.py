"""Shared test support for the curate module test suite.

Centralizes temporary workspace/DB construction, upstream item seeding,
config doubles, LLM response factories, HTTP doubles, and curate-table
snapshots so individual test files stay focused on the behavior under test.

IMPORTANT: `create_mock_upstream_tables` is a hand-written, minimal copy of
the upstream ingest/classify schema. It is NOT the canonical schema contract
and must never be treated as one; it only isolates curate-internal unit
tests. The authoritative upstream handoff contract is verified separately by
migration-based handoff tests using the active ingest/classify migrations.
"""

import json
import pathlib
import sqlite3
import tempfile
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from modules.curate.src.config import CurateConfig
from modules.curate.src.database import CurationRepository, get_connection

CURATE_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"
INGEST_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "ingest" / "src" / "migrations"
CLASSIFY_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "classify" / "src" / "migrations"


def make_temp_workspace(test_case: unittest.TestCase) -> pathlib.Path:
    """Creates a temporary workspace root and registers its cleanup.

    Place the test database at `<root>/data/canonical.db` so that the runner
    lock file (derived from `db_path.parent.parent`) also stays inside the
    temporary workspace.
    """
    temp_dir = tempfile.TemporaryDirectory()
    test_case.addCleanup(temp_dir.cleanup)
    return pathlib.Path(temp_dir.name)


def create_mock_upstream_tables(db_path: pathlib.Path, *, allow_draft_status: bool = False) -> None:
    """Creates the minimal upstream tables for isolated curate unit tests.

    This is a test-local convenience schema only; it is NOT the canonical
    ingest/classify schema contract (see module docstring). The authoritative
    contract is covered by migration-based handoff tests.

    `allow_draft_status` exists only so repository unit tests can seed a
    non-'ingested' row to verify queue filtering; the active ingest schema
    forbids any status other than 'ingested'.
    """
    ingest_status_values = "('ingested', 'draft')" if allow_draft_status else "('ingested')"
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS source_item (
                source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                canonical_url TEXT,
                ingest_status TEXT NOT NULL CHECK (ingest_status IN {ingest_status_values})
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item_text (
                source_item_text_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                sanitized_text TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS classification_result (
                classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                topic_class TEXT NOT NULL,
                classification_reason TEXT,
                governmental_involvement INTEGER,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


def seed_upstream_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    title: str,
    text: str,
    topic_class: str,
    ingest_status: str = "ingested",
    governmental_involvement: int = 0,
) -> None:
    """Seeds one upstream item with its text and classification rows.

    Callers must state the key queue-selection attributes (title, text,
    topic_class) explicitly so test preconditions stay visible. Commits the
    connection so the rows are visible to other connections.
    """
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO source_item (source_item_id, source_id, title, ingest_status) VALUES (?, 1, ?, ?)",
        (item_id, title, ingest_status),
    )
    cursor.execute(
        "INSERT INTO source_item_text (source_item_id, sanitized_text) VALUES (?, ?)",
        (item_id, text),
    )
    cursor.execute(
        "INSERT INTO classification_result (source_item_id, topic_class, classification_reason, governmental_involvement) VALUES (?, ?, 'seeded by test support', ?)",
        (item_id, topic_class, governmental_involvement),
    )
    conn.commit()


def build_test_config(
    *,
    supports_structured_output: bool,
    model_name: str = "gpt-5.4-mini",
    api_base: str = "https://api.test.com",
    api_key_env: str = "TEST_API_KEY",
    template_version: str = "curator_v1",
    system_instruction: str = "System Instruction",
    user_prompt_template: str = (
        "Title: {raw_title}, Text: {sanitized_text}, "
        "Topic: {topic_class}, Gov: {governmental_involvement}"
    ),
    temperature: float = 0.2,
    top_p: Optional[float] = 0.95,
    max_output_tokens: int = 2048,
    batch_size: int = 20,
    max_concurrent_requests: int = 3,
    rate_limit_per_minute: int = 600,
    request_timeout_seconds: float = 10.0,
    retry_attempts: int = 2,
    backoff_factor: float = 0.1,
) -> MagicMock:
    """Builds a mocked CurateConfig for orchestrator/LLM-client tests.

    The provider's structured-output capability is a mandatory explicit
    argument because it selects between the strict `json_schema` request path
    and the lenient `json_object` fallback path under test. Tests must derive
    their expectations from the values on this config object, not from the
    active workspace config.
    """
    config = MagicMock(spec=CurateConfig)
    config.active_provider_name = "test-provider"
    config.active_provider = MagicMock()
    config.active_provider.model_name = model_name
    config.active_provider.api_base = api_base
    config.active_provider.api_key_env = api_key_env
    config.active_provider.supports_structured_output = supports_structured_output

    config.active_template_name = "test-template"
    config.active_template = MagicMock()
    config.active_template.version = template_version
    config.active_template.system_instruction = system_instruction
    config.active_template.user_prompt_template = user_prompt_template

    config.execution_policy = MagicMock()
    config.execution_policy.batch_size = batch_size
    config.execution_policy.max_concurrent_requests = max_concurrent_requests
    config.execution_policy.rate_limit_per_minute = rate_limit_per_minute
    config.execution_policy.request_timeout_seconds = request_timeout_seconds
    config.execution_policy.retry_attempts = retry_attempts
    config.execution_policy.backoff_factor = backoff_factor

    config.request_defaults = MagicMock()
    config.request_defaults.temperature = temperature
    config.request_defaults.top_p = top_p
    config.request_defaults.max_output_tokens = max_output_tokens
    return config


def make_valid_response(
    downstream_action: str,
    *,
    decision_reason: Optional[str] = None,
    decision_overrides: Optional[Dict[str, Any]] = None,
    brief_overrides: Optional[Dict[str, Any]] = None,
    output_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Builds a schema-valid LLM curation response for one routing action.

    The downstream action is a mandatory explicit argument so every test
    states which routing branch it exercises. `*_overrides` dicts are merged
    shallowly into the corresponding section (use `None` values in
    `brief_overrides`/`output_overrides` to null out individual fields).
    """
    default_reasons = {
        "publish_link": "official_announcement",
        "publish_summary": "high_evidence_report",
        "edit_rewrite": "needs_rewrite_cleanup",
        "reject_discard": "duplicate",
    }
    if downstream_action not in default_reasons:
        raise ValueError(f"Unknown downstream_action for response factory: {downstream_action}")

    status = "approved" if downstream_action in ("publish_link", "publish_summary") else "rejected"

    decision: Dict[str, Any] = {
        "curate_status": status,
        "downstream_action": downstream_action,
        "decision_reason": decision_reason or default_reasons[downstream_action],
    }
    if decision_overrides:
        decision.update(decision_overrides)

    brief: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None

    if downstream_action != "reject_discard":
        brief = {
            "brief_goal": "Validate the core claims before publication",
            "target_format": (
                "link_card" if downstream_action == "publish_link" else "structured_summary"
            ),
            "key_claim": "Primary factual claim of the item",
            "key_evidence": "Official memorandum cited by the source",
            "required_context": "Prior congressional hearings on the topic",
            "risk_flags": ["sensationalist_source"],
            "tone_guidance": "neutral, factual",
        }
        if brief_overrides:
            brief.update(brief_overrides)

    if downstream_action in ("publish_link", "publish_summary"):
        output = {
            "display_title": "Clean De-Sensationalized Title",
            "summary_short": "A concise neutral summary paragraph.",
            "bullet_1": None,
            "bullet_2": None,
            "bullet_3": None,
        }
        if downstream_action == "publish_summary":
            output.update({
                "bullet_1": "Primary factual claim.",
                "bullet_2": "Evidence cited in the report.",
                "bullet_3": "Objective implication of the development.",
            })
        if output_overrides:
            output.update(output_overrides)

    return {
        "curation_decision": decision,
        "editor_brief": brief,
        "curation_output": output,
    }


def make_chat_completion_payload(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """Wraps a curation response object into the chat-completion envelope."""
    return {"choices": [{"message": {"content": json.dumps(response_data)}}]}


def make_mock_http_response(
    *,
    status_code: int = 200,
    json_data: Optional[Dict[str, Any]] = None,
    text: str = "",
) -> MagicMock:
    """Builds an httpx.Response double with explicit status and JSON body."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    return response


class FakeHTTPClient:
    """Minimal async HTTP double for fetch_llm_curation tests.

    Records every call and replays the queued outcomes in order; when the
    queue is exhausted the last outcome repeats. An outcome that is an
    Exception instance is raised instead of returned.
    """

    def __init__(self, outcomes: List[Any]):
        if not outcomes:
            raise ValueError("FakeHTTPClient requires at least one outcome")
        self._outcomes = list(outcomes)
        self.calls: List[Dict[str, Any]] = []

    async def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if len(self._outcomes) > 1:
            outcome = self._outcomes.pop(0)
        else:
            outcome = self._outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def snapshot_curate_tables(conn: sqlite3.Connection) -> Dict[str, List[Dict[str, Any]]]:
    """Returns a comparable full snapshot of the three curate-owned tables."""
    snapshot: Dict[str, List[Dict[str, Any]]] = {}
    cursor = conn.cursor()
    for table in ("curation_decision", "editor_brief", "curation_output"):
        cursor.execute(f"SELECT * FROM {table} ORDER BY source_item_id")
        snapshot[table] = [dict(row) for row in cursor.fetchall()]
    return snapshot


def seed_curation_state(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    curate_status: str,
    downstream_action: Optional[str],
    with_brief: bool = False,
    with_output: bool = False,
    decision_actor: str = "system",
    retry_count: int = 0,
    decision_reason: Optional[str] = None,
) -> None:
    """Seeds an explicit pre-existing curation state for transition tests.

    All key state preconditions (status, action, which side rows exist) are
    stated by the caller. Brief/output content uses recognizable "SEEDED"
    markers so tests can tell freshly written rows apart from stale ones.
    Commits the connection.
    """
    repo = CurationRepository(conn)
    repo.upsert_curation_decision({
        "source_item_id": item_id,
        "curate_status": curate_status,
        "downstream_action": downstream_action,
        "decision_reason": decision_reason or f"seeded {curate_status} state",
        "decision_actor": decision_actor,
        "retry_count": retry_count,
        "model_name": "seed-model",
        "prompt_version": "seed-v0",
    })
    if with_brief:
        repo.upsert_editor_brief({
            "source_item_id": item_id,
            "brief_goal": "SEEDED brief goal",
            "target_format": (
                "link_card" if downstream_action == "publish_link" else "structured_summary"
            ),
            "key_claim": "SEEDED claim",
            "key_evidence": None,
            "required_context": None,
            "risk_flags": ["seeded_flag"],
            "tone_guidance": "neutral",
        })
    if with_output:
        bullets = {"bullet_1": None, "bullet_2": None, "bullet_3": None}
        if downstream_action == "publish_summary":
            bullets = {
                "bullet_1": "SEEDED bullet claim",
                "bullet_2": "SEEDED bullet evidence",
                "bullet_3": "SEEDED bullet implication",
            }
        repo.upsert_curation_output({
            "source_item_id": item_id,
            "display_title": "SEEDED display title",
            "summary_short": "SEEDED summary",
            **bullets,
        })
    conn.commit()
