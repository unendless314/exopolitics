"""
Phase 1 contract tests for the five-field translation refactor.

Source contracts (all locked 2026-07-24):
- known_issues/TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md section 7.1
  (fingerprint / assembler / translate schema / translate quality /
  bypass+stale / failure safety rows)
- modules/translate/docs/DATA_CONTRACT.md section 1 (five-field schema)
  and section 2.1.1 (fingerprint serialization)
- modules/translate/docs/PROMPT_CONTRACT.md (translator_v2 response schema)
- modules/translate/docs/EXECUTION_POLICY.md section 5 (validation),
  section 6 (bypass), section 4 (failure safety)

These tests pin the TARGET contract. The runtime is still on the legacy
spliced `content_body` / `content` shape, so most of these tests are
EXPECTED TO FAIL (or error) until Phase 2 implements the target APIs.
That is the purpose of Phase 1: the failure reason must be the missing
target API, never a bug in the test code itself. Do not weaken these
tests to make them pass; implement the runtime instead.

Test-defined target APIs (docs lock behavior but not names; see module
docstring of the report / REFACTOR_PLAN Phase 2):
- modules/translate/src/approved_content_record.py:
    compute_content_fingerprint(display_title, summary_short,
                                bullet_1=None, bullet_2=None, bullet_3=None) -> str
- modules/translate/src/orchestrator.py:
    validate_translation_response(data, target_language_code=..., 
                                  source_summary=..., source_bullet_1=...,
                                  source_bullet_2=..., source_bullet_3=...,
                                  max_title_len=..., content_ratio_limit=...)
    (same function name as today, five-field signature)
- modules/translate/src/orchestrator.py translate_task and
  modules/translate/src/database.py TranslationRepository.upsert_translation_output
  keep their names and switch to five-field payloads.

Note on schema: the legacy migration file still creates `content_body` /
`content` columns, so this file creates the target five-field tables
inline (DDL copied from DATA_CONTRACT.md section 1.4) instead of calling
run_migrations(). Phase 2 may switch this helper to run_migrations() once
the v001 DDL is updated.
"""

import asyncio
import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import httpx

from modules.translate.src.config import TranslateConfig
from modules.translate.src.database import get_connection, TranslationRepository
from modules.translate.src import approved_content_record as handoff_module
from modules.translate.src.approved_content_record import assemble_approved_content_records
from modules.translate.src.orchestrator import translate_task, validate_translation_response


# ---------------------------------------------------------------------------
# Known UI label lists (label guard)
# English labels come from the legacy splice; zh/ja variants are the observed
# leaked variants from known_issues/TRANSLATION_LABEL_LEAKAGE.md section 4.2,
# mirrored in EXECUTION_POLICY.md section 5 rule 5.
# ---------------------------------------------------------------------------

EN_LABELS = ["Key Claim", "Evidence Level", "Objective Impact"]

ZH_LABEL_VARIANTS = [
    "主要主張", "關鍵主張", "核心主張",
    "證據層級", "證據等級",
    "客觀影響", "實際影響",
]

JA_LABEL_VARIANTS = [
    "主要な主張", "主要主張", "主張の要点",
    "証拠の水準", "証拠レベル", "証拠水準", "エビデンスレベル",
    "客観的な影響", "客観的影響", "目的上の影響",
]

# Union, deduplicated, order preserved (主要主張 appears in both zh and ja).
ALL_KNOWN_LABELS = list(dict.fromkeys(EN_LABELS + ZH_LABEL_VARIANTS + JA_LABEL_VARIANTS))


# ---------------------------------------------------------------------------
# Test-defined target API accessors
# ---------------------------------------------------------------------------

def compute_content_fingerprint(
    display_title: str,
    summary_short: str,
    bullet_1: Optional[str] = None,
    bullet_2: Optional[str] = None,
    bullet_3: Optional[str] = None,
) -> str:
    """Target API (Phase 2): the single shared five-field fingerprint helper.

    DATA_CONTRACT.md section 2.1.1 locks the algorithm (fixed field order,
    \\r\\n / \\r -> \\n normalization, NULL -> JSON null, fixed key order,
    no-whitespace JSON, UTF-8, SHA-256) and requires one shared helper used
    by both the assembler and tests, but does not name it. This test defines
    the name `compute_content_fingerprint` following the existing
    `compute_fingerprint` convention in approved_content_record.py.
    Raises AttributeError while the helper is not implemented.
    """
    return handoff_module.compute_content_fingerprint(
        display_title, summary_short, bullet_1, bullet_2, bullet_3
    )


def validate_v2(
    data: Dict[str, Any],
    target_language_code: str = "zh",
    source_summary: str = "A reasonably long English source summary for ratio checks.",
    source_bullet_1: Optional[str] = "First source bullet text.",
    source_bullet_2: Optional[str] = "Second source bullet text.",
    source_bullet_3: Optional[str] = "Third source bullet text.",
    max_title_len: int = 120,
    content_ratio_limit: float = 5.0,
) -> None:
    """Calls the Phase-2 target signature of validate_translation_response.

    Target signature (test-defined; keeps the existing function name in
    orchestrator.py and replaces `source_content_body` with the four source
    content fields needed for aggregate-ratio and nullability checks):
        validate_translation_response(
            data, target_language_code=..., source_summary=...,
            source_bullet_1=..., source_bullet_2=..., source_bullet_3=...,
            max_title_len=..., content_ratio_limit=...)
    Raises TypeError while the legacy single-body signature is in place.
    """
    return validate_translation_response(
        data,
        target_language_code=target_language_code,
        source_summary=source_summary,
        source_bullet_1=source_bullet_1,
        source_bullet_2=source_bullet_2,
        source_bullet_3=source_bullet_3,
        max_title_len=max_title_len,
        content_ratio_limit=content_ratio_limit,
    )


def five_field_response(
    title: str = "測試標題タイトル",
    summary: str = "這是摘要內容です。",
    bullet_1: Optional[str] = "這是第一個要點です。",
    bullet_2: Optional[str] = "這是第二個要點です。",
    bullet_3: Optional[str] = "這是第三個要點です。",
) -> Dict[str, Any]:
    """Builds a translator_v2 response payload valid for both zh and ja
    (aggregate contains CJK ideographs and kana)."""
    return {
        "translated_title": title,
        "translated_summary": summary,
        "translated_bullet_1": bullet_1,
        "translated_bullet_2": bullet_2,
        "translated_bullet_3": bullet_3,
    }


# ---------------------------------------------------------------------------
# Five-field schema helpers (DDL copied from DATA_CONTRACT.md section 1.4)
# ---------------------------------------------------------------------------

FIVE_FIELD_DDL = """
CREATE TABLE approved_content_record (
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

CREATE TABLE translation_output (
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
    translation_status TEXT NOT NULL CHECK (translation_status IN ('pending', 'completed', 'failed', 'stale')),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    translated_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_content_id) REFERENCES approved_content_record (parent_content_id) ON DELETE CASCADE,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id),
    UNIQUE (parent_content_id, language_code)
);
"""


def create_five_field_tables(db_path: pathlib.Path) -> None:
    """Creates the minimal upstream curate tables plus the target five-field
    approved_content_record / translation_output tables (inline DDL, see
    module docstring)."""
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
        cursor.executescript(FIVE_FIELD_DDL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Plan 7.1 row: fingerprint
# ---------------------------------------------------------------------------

class TestContentFingerprint(unittest.TestCase):
    """Fingerprint rules from DATA_CONTRACT.md 2.1.1 and plan 7.1:
    any five-field change or NULL<->empty change alters the fingerprint;
    line-ending normalization does not; the exact JSON serialization is
    pinned by independently computed SHA-256 golden vectors."""

    def test_golden_vector_ascii(self) -> None:
        # Independently hand-computed expectation: json.dumps with fixed key
        # order (display_title, summary_short, bullet_1, bullet_2, bullet_3),
        # no whitespace separators, UTF-8, SHA-256.
        payload = {
            "display_title": "Test Title",
            "summary_short": "Summary text.",
            "bullet_1": "Claim one.",
            "bullet_2": None,
            "bullet_3": None,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(
            serialized,
            '{"display_title":"Test Title","summary_short":"Summary text.",'
            '"bullet_1":"Claim one.","bullet_2":null,"bullet_3":null}',
        )
        expected = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        # Golden constant pins this exact serialization for Phase 2 review.
        self.assertEqual(
            expected,
            "0b0bbd33d2b68b4496dd0d17ea6ef5ac870a343561c8bb476158f92941e3c923",
        )
        self.assertEqual(
            compute_content_fingerprint("Test Title", "Summary text.", "Claim one.", None, None),
            expected,
        )

    def test_golden_vector_non_ascii_utf8(self) -> None:
        # Pins that non-ASCII text is hashed as raw UTF-8 (ensure_ascii=False),
        # not as \uXXXX escape sequences.
        payload = {
            "display_title": "測試標題",
            "summary_short": "這是摘要內容。",
            "bullet_1": None,
            "bullet_2": None,
            "bullet_3": None,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        expected = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        self.assertEqual(
            expected,
            "0893cb7b2d2de6ae9c413c900dda7838c38f93cd28f9ae5c645c7e328e5070b3",
        )
        self.assertEqual(
            compute_content_fingerprint("測試標題", "這是摘要內容。", None, None, None),
            expected,
        )

    def test_every_field_change_alters_fingerprint(self) -> None:
        base_args = ("Title", "Summary", "B1", "B2", "B3")
        base = compute_content_fingerprint(*base_args)
        mutations = [
            ("Title changed", "Summary", "B1", "B2", "B3"),
            ("Title", "Summary changed", "B1", "B2", "B3"),
            ("Title", "Summary", "B1 changed", "B2", "B3"),
            ("Title", "Summary", "B1", "B2 changed", "B3"),
            ("Title", "Summary", "B1", "B2", "B3 changed"),
        ]
        for mutated in mutations:
            self.assertNotEqual(
                compute_content_fingerprint(*mutated),
                base,
                f"fingerprint did not change for mutation {mutated!r}",
            )

    def test_null_vs_empty_string_alters_fingerprint(self) -> None:
        # NULL serializes as JSON null, empty string as ""; never conflated.
        self.assertNotEqual(
            compute_content_fingerprint("T", "S", None, None, None),
            compute_content_fingerprint("T", "S", "", "", ""),
        )
        # Each bullet slot is checked individually, so an implementation that
        # distinguishes NULL/empty only in bullet_1 is still caught.
        for index, slot in enumerate(("bullet_1", "bullet_2", "bullet_3"), start=2):
            base = ["T", "S", "B1", "B2", "B3"]
            base[index] = None
            mutated = list(base)
            mutated[index] = ""
            self.assertNotEqual(
                compute_content_fingerprint(*base),
                compute_content_fingerprint(*mutated),
                f"NULL vs empty string not distinguished in {slot}",
            )

    def test_line_ending_normalization_preserves_fingerprint(self) -> None:
        lf = compute_content_fingerprint("A\nB", "C\nD", "E\nF", None, None)
        crlf = compute_content_fingerprint("A\r\nB", "C\r\nD", "E\r\nF", None, None)
        cr = compute_content_fingerprint("A\rB", "C\rD", "E\rF", None, None)
        self.assertEqual(lf, crlf)
        self.assertEqual(lf, cr)
        # Same rule per bullet slot: \r\n and \r must normalize to \n in
        # bullet_2 and bullet_3 as well as bullet_1.
        for index, slot in enumerate(("bullet_1", "bullet_2", "bullet_3"), start=2):
            lf_args = ["T", "S", "B1", "B2", "B3"]
            crlf_args = list(lf_args)
            cr_args = list(lf_args)
            lf_args[index] = "X\nY"
            crlf_args[index] = "X\r\nY"
            cr_args[index] = "X\rY"
            self.assertEqual(
                compute_content_fingerprint(*lf_args),
                compute_content_fingerprint(*crlf_args),
                f"\\r\\n not normalized in {slot}",
            )
            self.assertEqual(
                compute_content_fingerprint(*lf_args),
                compute_content_fingerprint(*cr_args),
                f"\\r not normalized in {slot}",
            )


# ---------------------------------------------------------------------------
# Plan 7.1 row: assembler
# ---------------------------------------------------------------------------

class _FiveFieldDBTestCase(unittest.TestCase):
    """Base class providing a temp canonical DB with the five-field schema,
    seed helpers, and a translator_v2 mock config."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        create_five_field_tables(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -- seed helpers ------------------------------------------------------

    def seed_curation_approval(
        self,
        conn: sqlite3.Connection,
        item_id: int,
        title: str,
        summary: str,
        b1: Optional[str] = None,
        b2: Optional[str] = None,
        b3: Optional[str] = None,
        updated_at: str = "2026-06-20T12:00:00Z",
    ) -> None:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO source_item (source_item_id, source_id, title, ingest_status) "
            "VALUES (?, 1, ?, 'ingested')",
            (item_id, title),
        )
        action = "publish_summary" if (b1 and b2 and b3) else "publish_link"
        cursor.execute(
            "INSERT INTO curation_decision (source_item_id, curate_status, downstream_action, "
            "model_name, prompt_version, curated_at, created_at) "
            "VALUES (?, 'approved', ?, 'curator', 'v1', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z')",
            (item_id, action),
        )
        cursor.execute(
            "INSERT INTO curation_output (source_item_id, display_title, summary_short, "
            "bullet_1, bullet_2, bullet_3, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '2026-06-20T12:00:00Z', ?)",
            (item_id, title, summary, b1, b2, b3, updated_at),
        )
        conn.commit()

    def seed_approved_record(
        self,
        conn: sqlite3.Connection,
        parent_content_id: int = 1,
        source_item_id: int = 100,
        display_title: str = "English Title",
        summary_short: str = "English summary content.",
        bullet_1: Optional[str] = "Claim content.",
        bullet_2: Optional[str] = "Evidence content.",
        bullet_3: Optional[str] = "Impact content.",
        fingerprint: str = "fp_test",
        language: str = "en",
    ) -> None:
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
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z')",
            (
                parent_content_id, source_item_id, display_title, summary_short,
                bullet_1, bullet_2, bullet_3, fingerprint, language,
            ),
        )
        conn.commit()

    def seed_translation_row(
        self,
        conn: sqlite3.Connection,
        parent_content_id: int = 1,
        source_item_id: int = 100,
        language_code: str = "zh",
        display_title: Optional[str] = None,
        summary_short: Optional[str] = None,
        bullet_1: Optional[str] = None,
        bullet_2: Optional[str] = None,
        bullet_3: Optional[str] = None,
        source_fingerprint: str = "fp_test",
        status: str = "completed",
        retry_count: int = 0,
        model_name: str = "gpt-5.4-mini",
        prompt_version: str = "translator_v2",
        translated_at: Optional[str] = "2026-06-20T12:00:00Z",
    ) -> None:
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

    # -- builders ----------------------------------------------------------

    def build_config(self) -> MagicMock:
        """Mock TranslateConfig for the translator_v2 five-field contract."""
        config = MagicMock(spec=TranslateConfig)
        config.active_provider_name = "test-provider"
        config.active_provider = MagicMock()
        config.active_provider.model_name = "gpt-5.4-mini"
        config.active_provider.api_base = "https://api.test.com"
        config.active_provider.api_key_env = "TEST_API_KEY"
        config.active_provider.supports_structured_output = False

        config.active_template = MagicMock()
        config.active_template.version = "translator_v2"
        config.active_template.system_instruction = "System Instruction"
        config.active_template.user_prompt_template = (
            "Translate the following article fields to target language: {target_language}\n"
            "Source Title:\n{display_title}\n"
            "Source Summary:\n{summary_short}\n"
            "Source Bullet 1 (factual claim):\n{bullet_1}\n"
            "Source Bullet 2 (evidence level):\n{bullet_2}\n"
            "Source Bullet 3 (objective implication):\n{bullet_3}"
        )

        config.execution_policy = MagicMock()
        config.execution_policy.batch_size = 20
        config.execution_policy.max_concurrent_requests = 3
        config.execution_policy.rate_limit_per_minute = 60
        config.execution_policy.request_timeout_seconds = 10.0
        config.execution_policy.retry_attempts = 3
        config.execution_policy.backoff_factor = 0.1

        config.request_defaults = MagicMock()
        config.request_defaults.temperature = 0.3
        config.request_defaults.top_p = 0.95
        config.request_defaults.max_output_tokens = 4096

        config.target_languages = {
            "en": MagicMock(label="English", max_title_length=500),
            "ja": MagicMock(label="Japanese", max_title_length=120),
            "zh": MagicMock(label="Traditional Chinese", max_title_length=120),
        }

        config.validation = MagicMock()
        config.validation.default_max_title_length = 500
        config.validation.content_ratio_limit = 1.2
        return config

    def make_task(
        self,
        parent_content_id: int = 1,
        source_item_id: int = 100,
        language_code: str = "en",
        source_language: str = "en",
        status: str = "new",
        display_title: str = "English Title",
        summary_short: str = "English summary content.",
        bullet_1: Optional[str] = "Claim content.",
        bullet_2: Optional[str] = "Evidence content.",
        bullet_3: Optional[str] = "Impact content.",
        fingerprint: str = "fp_test",
    ) -> Dict[str, Any]:
        """Five-field translation task payload (target shape for
        get_pending_translation_tasks / single-task mode)."""
        return {
            "parent_content_id": parent_content_id,
            "source_item_id": source_item_id,
            "display_title": display_title,
            "summary_short": summary_short,
            "bullet_1": bullet_1,
            "bullet_2": bullet_2,
            "bullet_3": bullet_3,
            "content_fingerprint": fingerprint,
            "content_language_code": source_language,
            "language_code": language_code,
            "status": status,
            "retry_count": 0,
        }


class TestHandoffAssemblerFiveField(_FiveFieldDBTestCase):
    """Plan 7.1 assembler row: publish_summary passes the five fields straight
    through; publish_link stores three NULL bullets; no UI label is ever
    injected into any stored field."""

    def test_publish_summary_passes_through_five_fields_without_labels(self) -> None:
        conn = get_connection(self.db_path)
        try:
            self.seed_curation_approval(
                conn,
                item_id=10,
                title="Mother-draft Title One",
                summary="This is a brief summary content.",
                b1="Claim content.",
                b2="Evidence content.",
                b3="Impact content.",
            )

            stats = assemble_approved_content_records(conn)
            self.assertEqual(stats["scanned"], 1)
            self.assertEqual(stats["inserted"], 1)

            row = conn.execute(
                "SELECT * FROM approved_content_record WHERE source_item_id = 10"
            ).fetchone()
            self.assertIsNotNone(row)

            # Straight-through copy: each stored field equals curation_output exactly.
            self.assertEqual(row["display_title"], "Mother-draft Title One")
            self.assertEqual(row["summary_short"], "This is a brief summary content.")
            self.assertEqual(row["bullet_1"], "Claim content.")
            self.assertEqual(row["bullet_2"], "Evidence content.")
            self.assertEqual(row["bullet_3"], "Impact content.")

            # Fingerprint is the five-field helper output, computed at write time.
            self.assertEqual(
                row["content_fingerprint"],
                compute_content_fingerprint(
                    "Mother-draft Title One",
                    "This is a brief summary content.",
                    "Claim content.",
                    "Evidence content.",
                    "Impact content.",
                ),
            )

            # No UI presentation label may appear in any stored content field.
            for field in ("display_title", "summary_short", "bullet_1", "bullet_2", "bullet_3"):
                value = row[field] or ""
                for label in EN_LABELS:
                    self.assertNotIn(label, value, f"{label} leaked into {field}")
        finally:
            conn.close()

    def test_publish_link_has_three_null_bullets(self) -> None:
        conn = get_connection(self.db_path)
        try:
            self.seed_curation_approval(
                conn,
                item_id=20,
                title="Mother-draft Title Two",
                summary="This is a link sharing article.",
                b1=None,
                b2=None,
                b3=None,
            )

            stats = assemble_approved_content_records(conn)
            self.assertEqual(stats["inserted"], 1)

            row = conn.execute(
                "SELECT * FROM approved_content_record WHERE source_item_id = 20"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["display_title"], "Mother-draft Title Two")
            self.assertEqual(row["summary_short"], "This is a link sharing article.")
            self.assertIsNone(row["bullet_1"])
            self.assertIsNone(row["bullet_2"])
            self.assertIsNone(row["bullet_3"])
            self.assertEqual(
                row["content_fingerprint"],
                compute_content_fingerprint(
                    "Mother-draft Title Two", "This is a link sharing article.", None, None, None
                ),
            )
            for label in EN_LABELS:
                self.assertNotIn(label, row["summary_short"])
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Plan 7.1 row: translate schema (response validation)
# ---------------------------------------------------------------------------

class TestResponseSchemaValidation(unittest.TestCase):
    """PROMPT_CONTRACT.md section 3/5: five required keys, trimmed non-empty
    title/summary, null-in/null-out bullets (checked per bullet slot in both
    directions), rejection of partial bullets, whitespace-only values, and
    non-string / null / non-dict payloads."""

    def test_valid_full_response_passes(self) -> None:
        validate_v2(five_field_response(), target_language_code="zh")
        validate_v2(five_field_response(), target_language_code="ja")

    def test_valid_null_bullet_response_passes(self) -> None:
        data = five_field_response(bullet_1=None, bullet_2=None, bullet_3=None)
        validate_v2(
            data,
            target_language_code="zh",
            source_bullet_1=None,
            source_bullet_2=None,
            source_bullet_3=None,
        )

    def test_missing_any_of_five_keys_is_rejected(self) -> None:
        keys = [
            "translated_title",
            "translated_summary",
            "translated_bullet_1",
            "translated_bullet_2",
            "translated_bullet_3",
        ]
        for key in keys:
            data = five_field_response()
            del data[key]
            with self.assertRaises(ValueError, msg=f"missing key: {key}"):
                validate_v2(data)

    def test_whitespace_only_title_or_summary_is_rejected(self) -> None:
        for whitespace in ("   ", " \t\n "):
            data = five_field_response(title=whitespace)
            with self.assertRaises(ValueError, msg=f"title={whitespace!r}"):
                validate_v2(data)
            data = five_field_response(summary=whitespace)
            with self.assertRaises(ValueError, msg=f"summary={whitespace!r}"):
                validate_v2(data)

    def test_whitespace_only_bullet_is_rejected(self) -> None:
        data = five_field_response(bullet_2="   ")
        with self.assertRaises(ValueError):
            validate_v2(data)

    def test_null_in_requires_null_out(self) -> None:
        # Source bullet NULL but translated bullet non-null -> reject.
        data = five_field_response()
        with self.assertRaises(ValueError):
            validate_v2(data, source_bullet_1=None, source_bullet_2=None, source_bullet_3=None)

    def test_null_in_requires_null_out_per_bullet(self) -> None:
        # Each source bullet slot individually: only that source bullet is
        # NULL and the other two slots match on both sides, so the reject
        # must come from that single slot. An implementation checking only
        # bullet_1 (or only the all-three-null case) is caught here.
        for slot in ("bullet_1", "bullet_2", "bullet_3"):
            data = five_field_response()
            with self.assertRaises(ValueError, msg=f"single null source slot: {slot}"):
                validate_v2(data, **{f"source_{slot}": None})

    def test_non_null_in_requires_non_null_out_per_bullet(self) -> None:
        # Mirror image: only one source bullet is non-null and all translated
        # bullets are null, so the mismatch is isolated to that single slot.
        for slot in ("bullet_1", "bullet_2", "bullet_3"):
            source_kwargs = {f"source_{s}": None for s in ("bullet_1", "bullet_2", "bullet_3")}
            source_kwargs[f"source_{slot}"] = "Source bullet text."
            data = five_field_response(bullet_1=None, bullet_2=None, bullet_3=None)
            with self.assertRaises(ValueError, msg=f"single non-null source slot: {slot}"):
                validate_v2(data, **source_kwargs)

    def test_non_null_in_requires_non_null_out(self) -> None:
        # Source bullets non-null: each single null translated bullet is rejected.
        for slot in ("bullet_1", "bullet_2", "bullet_3"):
            data = five_field_response(**{slot: None})
            with self.assertRaises(ValueError, msg=f"nulled slot: {slot}"):
                validate_v2(data)

    def test_partial_bullet_set_is_rejected(self) -> None:
        # Explicit partial-population case from the plan: middle bullet null only.
        data = five_field_response(bullet_2=None)
        with self.assertRaises(ValueError):
            validate_v2(data)

    def test_null_title_or_summary_is_rejected(self) -> None:
        # Only bullets may be null; title/summary must be non-null strings
        # (distinct from the missing-key cases, which delete the key entirely).
        data = five_field_response(title=None)
        with self.assertRaises(ValueError, msg="title=None"):
            validate_v2(data)
        data = five_field_response(summary=None)
        with self.assertRaises(ValueError, msg="summary=None"):
            validate_v2(data)

    def test_non_string_title_or_summary_is_rejected(self) -> None:
        # title/summary must be non-empty strings; any non-string type fails.
        for bad_value in (123, ["x"], {"k": "v"}, True):
            data = five_field_response(title=bad_value)
            with self.assertRaises(ValueError, msg=f"title={bad_value!r}"):
                validate_v2(data)
            data = five_field_response(summary=bad_value)
            with self.assertRaises(ValueError, msg=f"summary={bad_value!r}"):
                validate_v2(data)

    def test_non_string_bullet_is_rejected(self) -> None:
        # Bullets are null-or-non-empty-string; every other type is rejected.
        # Each slot is checked individually so a bullet_1-only type check is
        # caught.
        for slot in ("bullet_1", "bullet_2", "bullet_3"):
            for bad_value in (123, ["x"], {"k": "v"}, True):
                data = five_field_response(**{slot: bad_value})
                with self.assertRaises(ValueError, msg=f"{slot}={bad_value!r}"):
                    validate_v2(data)

    def test_non_dict_response_is_rejected(self) -> None:
        # The payload itself must be a mapping; anything else is a validation
        # failure (ValueError), not a pass and not an incidental exception.
        for bad_data in (None, "translated_title", ["translated_title"], 42):
            with self.assertRaises(ValueError, msg=f"data={bad_data!r}"):
                validate_v2(bad_data)


# ---------------------------------------------------------------------------
# Plan 7.1 row: translate quality (title cap, aggregate ratio, script, label guard)
# ---------------------------------------------------------------------------

class TestTranslationQualityValidation(unittest.TestCase):
    """EXECUTION_POLICY.md section 5 rules 1-5."""

    def test_title_length_cap(self) -> None:
        data = five_field_response(title="T" * 11)
        with self.assertRaises(ValueError):
            validate_v2(data, max_title_len=10)

    def test_japanese_title_120_cap(self) -> None:
        # ja limit is canonically configured as max_title_length=120.
        over = five_field_response(title="あ" * 121)
        with self.assertRaises(ValueError):
            validate_v2(over, target_language_code="ja", max_title_len=120)
        # Boundary: exactly 120 characters passes the cap.
        at_cap = five_field_response(title="あ" * 120)
        validate_v2(at_cap, target_language_code="ja", max_title_len=120)

    def test_aggregate_ratio_limit(self) -> None:
        # Source aggregate = 60 + 10*3 = 90 chars; response aggregate =
        # 60 + 20*3 = 120 chars; 120/90 = 1.33 > 1.2 -> reject.
        data = five_field_response(
            title="標題",
            summary="這" + "T" * 59,
            bullet_1="這" + "R" * 19,
            bullet_2="這" + "R" * 19,
            bullet_3="這" + "R" * 19,
        )
        with self.assertRaises(ValueError):
            validate_v2(
                data,
                source_summary="S" * 60,
                source_bullet_1="B" * 10,
                source_bullet_2="B" * 10,
                source_bullet_3="B" * 10,
                content_ratio_limit=1.2,
            )

    def test_ratio_is_computed_over_aggregate_not_single_bullet(self) -> None:
        # Each single bullet expands 5 -> 30 chars (6x, far above the limit),
        # but the aggregate ratio is 190/115 = 1.65 < 2.0, so this must pass.
        data = five_field_response(
            title="標題",
            summary="這" + "T" * 99,
            bullet_1="這" + "R" * 29,
            bullet_2="這" + "R" * 29,
            bullet_3="這" + "R" * 29,
        )
        validate_v2(
            data,
            source_summary="S" * 100,
            source_bullet_1="B" * 5,
            source_bullet_2="B" * 5,
            source_bullet_3="B" * 5,
            content_ratio_limit=2.0,
        )

    def test_zh_requires_at_least_one_cjk_character(self) -> None:
        valid = five_field_response(title="標題", summary="這是中文摘要。")
        validate_v2(valid, target_language_code="zh")

        invalid = five_field_response(
            title="Title",
            summary="English only summary.",
            bullet_1="English only bullet one.",
            bullet_2="English only bullet two.",
            bullet_3="English only bullet three.",
        )
        with self.assertRaises(ValueError):
            validate_v2(invalid, target_language_code="zh")

    def test_script_check_applies_to_aggregate_excluding_title(self) -> None:
        # CJK present only in the title must not satisfy the zh script rule;
        # the check runs over aggregated summary + non-null bullets.
        data = five_field_response(
            title="中文標題",
            summary="English only summary.",
            bullet_1="English only bullet one.",
            bullet_2="English only bullet two.",
            bullet_3="English only bullet three.",
        )
        with self.assertRaises(ValueError):
            validate_v2(data, target_language_code="zh")

    def test_ja_requires_at_least_one_kana(self) -> None:
        valid = five_field_response(title="標題", summary="これは日本語の摘要です。")
        validate_v2(valid, target_language_code="ja")

        # Proper nouns / acronyms may stay in English.
        mixed = five_field_response(title="報告", summary="AAROによるUAPに関する報告書です。")
        validate_v2(mixed, target_language_code="ja")

        # Kanji-only content without any hiragana/katakana is rejected.
        kanji_only = five_field_response(
            title="標題",
            summary="日本語摘要內容",
            bullet_1="第一要点內容",
            bullet_2="第二要点內容",
            bullet_3="第三要点內容",
        )
        with self.assertRaises(ValueError):
            validate_v2(kanji_only, target_language_code="ja")

        english_only = five_field_response(
            title="Title",
            summary="English only summary.",
            bullet_1="English only bullet one.",
            bullet_2="English only bullet two.",
            bullet_3="English only bullet three.",
        )
        with self.assertRaises(ValueError):
            validate_v2(english_only, target_language_code="ja")

    def test_label_guard_rejects_all_known_label_prefixes(self) -> None:
        # Guard list = English labels + every observed zh/ja variant from
        # TRANSLATION_LABEL_LEAKAGE.md 4.2, applied to zh and ja targets.
        for lang in ("zh", "ja"):
            for label in ALL_KNOWN_LABELS:
                data = five_field_response(summary=f"{label}: これはテスト內容です。")
                with self.assertRaises(ValueError, msg=f"lang={lang} label={label}"):
                    validate_v2(data, target_language_code=lang)

    def test_label_guard_strips_whitespace_and_markdown_decorations(self) -> None:
        # Leading whitespace, optional Markdown emphasis / list markers, and
        # fullwidth colons must not defeat the guard.
        decorated_forms = [
            "  {label}: 內容です。",
            "**{label}**: 內容です。",
            "* {label}: 內容です。",
            "- **{label}**: 內容です。",
            "{label}：內容です。",
            "**{label}**：內容です。",
        ]
        for lang in ("zh", "ja"):
            for label in ("Key Claim", "關鍵主張", "証拠レベル"):
                for form in decorated_forms:
                    data = five_field_response(summary=form.format(label=label))
                    with self.assertRaises(
                        ValueError, msg=f"lang={lang} label={label} form={form}"
                    ):
                        validate_v2(data, target_language_code=lang)

    def test_label_guard_applies_to_bullets(self) -> None:
        for lang in ("zh", "ja"):
            data = five_field_response(bullet_2="**Evidence Level**: 內容です。")
            with self.assertRaises(ValueError, msg=f"lang={lang}"):
                validate_v2(data, target_language_code=lang)

    def test_label_guard_allows_non_prefix_mentions(self) -> None:
        for lang in ("zh", "ja"):
            # Label words appearing mid-sentence are content, not prefixes.
            data = five_field_response(summary="本報告說明主要主張與證據等級的內容です。")
            validate_v2(data, target_language_code=lang)
            # Label-like prefix without a following colon is not rejected.
            data = five_field_response(summary="主要主張是本文的重點內容です。")
            validate_v2(data, target_language_code=lang)

    def test_label_guard_is_not_applied_to_english_target(self) -> None:
        # EXECUTION_POLICY 5.5 scopes the guard to zh/ja only.
        data = five_field_response(
            title="English Title",
            summary="Key Claim: The filing shows the dispute.",
            bullet_1="Evidence Level: Multiple witnesses.",
            bullet_2="Objective Impact: Sets a precedent.",
            bullet_3="A regular third bullet.",
        )
        validate_v2(data, target_language_code="en", max_title_len=500)


# ---------------------------------------------------------------------------
# Plan 7.1 row: bypass / stale
# ---------------------------------------------------------------------------

class TestBypassAndStale(_FiveFieldDBTestCase):
    """EXECUTION_POLICY.md section 6 and DATA_CONTRACT.md 2.2: en bypass copies
    the five fields with zero API calls and bypass marker fields; config
    changes must not stale bypass rows; fingerprint mismatch still does."""

    @patch("httpx.AsyncClient.post")
    def test_bypass_copies_five_fields_without_api_call(self, mock_post) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(conn, language="en")

            task = self.make_task(language_code="en", source_language="en")
            db_lock = asyncio.Lock()
            client = httpx.AsyncClient()

            success = asyncio.run(
                translate_task(
                    repo=repo,
                    client=client,
                    config=self.build_config(),
                    task=task,
                    api_key="mock",
                    db_lock=db_lock,
                    commit=True,
                )
            )
            self.assertTrue(success)
            mock_post.assert_not_called()

            row = repo.get_translation_output(1, "en")
            self.assertIsNotNone(row)
            self.assertEqual(row["translation_status"], "completed")
            self.assertEqual(row["model_name"], "bypass")
            self.assertEqual(row["prompt_version"], "bypass")
            self.assertEqual(row["retry_count"], 0)
            self.assertIsNotNone(row["translated_at"])
            self.assertEqual(row["source_fingerprint"], "fp_test")

            # Five-field straight copy from the mother-draft.
            self.assertEqual(row["display_title"], "English Title")
            self.assertEqual(row["summary_short"], "English summary content.")
            self.assertEqual(row["bullet_1"], "Claim content.")
            self.assertEqual(row["bullet_2"], "Evidence content.")
            self.assertEqual(row["bullet_3"], "Impact content.")

            # Bypassed content must not gain UI labels.
            for field in ("display_title", "summary_short", "bullet_1", "bullet_2", "bullet_3"):
                value = row[field] or ""
                for label in EN_LABELS:
                    self.assertNotIn(label, value, f"{label} leaked into {field}")
        finally:
            conn.close()

    @patch("httpx.AsyncClient.post")
    def test_bypass_copies_null_bullets_for_publish_link(self, mock_post) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(
                conn, language="en", bullet_1=None, bullet_2=None, bullet_3=None
            )

            task = self.make_task(
                language_code="en",
                source_language="en",
                bullet_1=None,
                bullet_2=None,
                bullet_3=None,
            )
            db_lock = asyncio.Lock()
            client = httpx.AsyncClient()

            success = asyncio.run(
                translate_task(
                    repo=repo,
                    client=client,
                    config=self.build_config(),
                    task=task,
                    api_key="mock",
                    db_lock=db_lock,
                    commit=True,
                )
            )
            self.assertTrue(success)
            mock_post.assert_not_called()

            row = repo.get_translation_output(1, "en")
            self.assertIsNotNone(row)
            self.assertEqual(row["model_name"], "bypass")
            self.assertEqual(row["prompt_version"], "bypass")
            self.assertEqual(row["summary_short"], "English summary content.")
            self.assertIsNone(row["bullet_1"])
            self.assertIsNone(row["bullet_2"])
            self.assertIsNone(row["bullet_3"])
        finally:
            conn.close()

    def test_config_change_does_not_stale_bypass(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(conn, language="en", fingerprint="fp_bypass")
            self.seed_translation_row(
                conn,
                language_code="en",
                display_title="English Title",
                summary_short="English summary content.",
                bullet_1="Claim content.",
                bullet_2="Evidence content.",
                bullet_3="Impact content.",
                source_fingerprint="fp_bypass",
                model_name="bypass",
                prompt_version="bypass",
            )

            # Running with a different model/prompt version must not stale the
            # bypass row.
            staled = repo.detect_and_mark_stale("gpt-different-model", "translator_v9")
            self.assertEqual(staled, [])

            row = repo.get_translation_output(1, "en")
            self.assertEqual(row["translation_status"], "completed")
        finally:
            conn.close()

    def test_fingerprint_mismatch_still_stales_bypass(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(conn, language="en", fingerprint="fp_bypass")
            self.seed_translation_row(
                conn,
                language_code="en",
                display_title="English Title",
                summary_short="English summary content.",
                bullet_1="Claim content.",
                bullet_2="Evidence content.",
                bullet_3="Impact content.",
                source_fingerprint="fp_bypass",
                model_name="bypass",
                prompt_version="bypass",
            )

            # Mother-draft edited upstream -> fingerprint mismatch -> stale.
            conn.execute(
                "UPDATE approved_content_record SET content_fingerprint = 'fp_edited' "
                "WHERE parent_content_id = 1"
            )
            conn.commit()

            staled = repo.detect_and_mark_stale("gpt-5.4-mini", "translator_v2")
            self.assertIn((1, "en", "fingerprint_mismatch"), staled)

            row = repo.get_translation_output(1, "en")
            self.assertEqual(row["translation_status"], "stale")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Plan 7.1 row: failure safety
# ---------------------------------------------------------------------------

class TestFailureSafety(_FiveFieldDBTestCase):
    """EXECUTION_POLICY.md section 4: first failure stores five NULL content
    fields; operator-forced re-run failure must not overwrite the last
    successful five-field content."""

    @patch("httpx.AsyncClient.post")
    def test_first_failure_keeps_five_fields_null(self, mock_post) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(conn, language="en")
            mock_post.side_effect = httpx.ConnectError("API unavailable")

            task = self.make_task(language_code="zh", source_language="en", status="new")
            db_lock = asyncio.Lock()
            client = httpx.AsyncClient()

            success = asyncio.run(
                translate_task(
                    repo=repo,
                    client=client,
                    config=self.build_config(),
                    task=task,
                    api_key="mock",
                    db_lock=db_lock,
                    commit=True,
                )
            )
            self.assertFalse(success)

            row = repo.get_translation_output(1, "zh")
            self.assertIsNotNone(row)
            self.assertEqual(row["translation_status"], "failed")
            self.assertEqual(row["retry_count"], 1)
            # First-run failure: all five content fields stay NULL; no empty
            # strings or partial content may be written.
            self.assertIsNone(row["display_title"])
            self.assertIsNone(row["summary_short"])
            self.assertIsNone(row["bullet_1"])
            self.assertIsNone(row["bullet_2"])
            self.assertIsNone(row["bullet_3"])
            self.assertIsNone(row["translated_at"])
        finally:
            conn.close()

    @patch("httpx.AsyncClient.post")
    def test_forced_rerun_failure_preserves_successful_five_fields(self, mock_post) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = TranslationRepository(conn)
            self.seed_approved_record(conn, language="en")
            self.seed_translation_row(
                conn,
                language_code="zh",
                display_title="舊的成功標題",
                summary_short="舊的成功摘要。",
                bullet_1="舊的要點一。",
                bullet_2="舊的要點二。",
                bullet_3="舊的要點三。",
                status="completed",
                retry_count=0,
                translated_at="2026-06-20T12:00:00Z",
            )
            mock_post.side_effect = httpx.ConnectError("API unavailable")

            # Operator-forced re-run of an already completed row.
            task = self.make_task(language_code="zh", source_language="en", status="completed")
            db_lock = asyncio.Lock()
            client = httpx.AsyncClient()

            success = asyncio.run(
                translate_task(
                    repo=repo,
                    client=client,
                    config=self.build_config(),
                    task=task,
                    api_key="mock",
                    db_lock=db_lock,
                    commit=True,
                )
            )
            self.assertFalse(success)

            row = repo.get_translation_output(1, "zh")
            self.assertEqual(row["translation_status"], "completed")
            self.assertEqual(row["display_title"], "舊的成功標題")
            self.assertEqual(row["summary_short"], "舊的成功摘要。")
            self.assertEqual(row["bullet_1"], "舊的要點一。")
            self.assertEqual(row["bullet_2"], "舊的要點二。")
            self.assertEqual(row["bullet_3"], "舊的要點三。")
            self.assertEqual(row["retry_count"], 0)
            self.assertEqual(row["translated_at"], "2026-06-20T12:00:00Z")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
