import asyncio
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from typing import Optional, Any
import httpx

from modules.classify.src.config import validate_and_load_config, ClassifyConfig
from modules.classify.src.database import (
    run_migrations,
    get_connection,
    ClassificationResultRepository,
    transaction
)
from modules.classify.src.orchestrator import (
    orchestrate_run,
    validate_classification_response,
    fetch_llm_classification,
    classify_item
)

DEFAULT_CLASSIFY_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

def create_mock_ingest_tables(db_path: pathlib.Path) -> None:
    """Helper to seed the minimal schema required for upstream ingest tables."""
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


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_settings_yaml(self, content: str) -> None:
        with open(self.config_dir / "model_settings.yaml", "w", encoding="utf-8") as f:
            f.write(content)

    def write_templates_yaml(self, content: str) -> None:
        with open(self.config_dir / "prompt_templates.yaml", "w", encoding="utf-8") as f:
            f.write(content)

    def test_valid_configuration(self) -> None:
        self.write_settings_yaml("""
active_provider: mini-proxy
active_prompt_template: single_item_v4
request_defaults:
  temperature: 0.1
  top_p: 0.95
  max_output_tokens: 1024
execution_policy:
  batch_size: 20
  max_concurrent_requests: 3
  rate_limit_per_minute: 60
  request_timeout_seconds: 45.0
  retry_attempts: 3
  backoff_factor: 2.0
providers:
  mini-proxy:
    api_type: openai_compatible
    api_key_env: MINI_API_KEY
    model_name: gpt-5.4-mini
    supports_structured_output: true

""")
        self.write_templates_yaml("""
templates:
  single_item_v4:
    version: v4.0
    description: Test prompt template
    system_instruction: You are a classifier.
    user_prompt_template: "Title: {title}, Text: {sanitized_text}"
""")
        config = validate_and_load_config(self.config_dir)
        self.assertEqual(config.active_provider_name, "mini-proxy")
        self.assertEqual(config.active_provider.model_name, "gpt-5.4-mini")
        self.assertEqual(config.active_template.version, "v4.0")

    def test_missing_provider(self) -> None:
        self.write_settings_yaml("""
active_provider: non-existent-provider
active_prompt_template: single_item_v4
request_defaults:
  temperature: 0.1
  top_p: 0.95
  max_output_tokens: 1024
execution_policy:
  batch_size: 20
  max_concurrent_requests: 3
  rate_limit_per_minute: 60
  request_timeout_seconds: 45.0
  retry_attempts: 3
  backoff_factor: 2.0
providers:
  mini-proxy:
    api_type: openai_compatible
    api_key_env: MINI_API_KEY
    model_name: gpt-5.4-mini
    supports_structured_output: true

""")
        self.write_templates_yaml("""
templates:
  single_item_v4:
    version: v4.0
    system_instruction: You are a classifier.
    user_prompt_template: "Title: {title}, Text: {sanitized_text}"
""")
        with self.assertRaises(ValueError):
            validate_and_load_config(self.config_dir)

    def test_invalid_temperature(self) -> None:
        self.write_settings_yaml("""
active_provider: mini-proxy
active_prompt_template: single_item_v4
request_defaults:
  temperature: 2.5 # invalid (must be <= 2.0)
  top_p: 0.95
  max_output_tokens: 1024
execution_policy:
  batch_size: 20
  max_concurrent_requests: 3
  rate_limit_per_minute: 60
  request_timeout_seconds: 45.0
  retry_attempts: 3
  backoff_factor: 2.0
providers:
  mini-proxy:
    api_type: openai_compatible
    api_key_env: MINI_API_KEY
    model_name: gpt-5.4-mini

""")
        self.write_templates_yaml("""
templates:
  single_item_v4:
    version: v4.0
    system_instruction: You are a classifier.
    user_prompt_template: "Title: {title}, Text: {sanitized_text}"
""")
        with self.assertRaises(ValueError):
            validate_and_load_config(self.config_dir)

    def test_invalid_top_p(self) -> None:
        self.write_settings_yaml("""
active_provider: mini-proxy
active_prompt_template: single_item_v4
request_defaults:
  temperature: 0.5
  top_p: 5.0 # invalid (must be <= 1.0)
  max_output_tokens: 1024
execution_policy:
  batch_size: 20
  max_concurrent_requests: 3
  rate_limit_per_minute: 60
  request_timeout_seconds: 45.0
  retry_attempts: 3
  backoff_factor: 2.0
providers:
  mini-proxy:
    api_type: openai_compatible
    api_key_env: MINI_API_KEY
    model_name: gpt-5.4-mini

""")
        self.write_templates_yaml("""
templates:
  single_item_v4:
    version: v4.0
    system_instruction: You are a classifier.
    user_prompt_template: "Title: {title}, Text: {sanitized_text}"
""")
        with self.assertRaises(ValueError):
            validate_and_load_config(self.config_dir)



class TestPromptAndPolicy(unittest.TestCase):
    def test_additional_signals_filtering(self) -> None:
        # 1. Valid response with all allowlisted signals
        raw_response = {
            "topic_class": "core",
            "classification_confidence": 0.9,
            "classification_reason": "Direct reference to declassified sensor video.",
            "content_density": "high",
            "source_text_quality": "strong",
            "primary_language_code": "en",
            "governmental_involvement": 1,
            "content_timeliness": "current",
            "primary_evidence_type": "radar_sensor",
            "unauthorized_key": "some_value" # Should be discarded
        }
        
        stable, extra = validate_classification_response(raw_response)
        
        # Verify stable fields
        self.assertEqual(stable["topic_class"], "core")
        self.assertEqual(stable["governmental_involvement"], 1)
        self.assertEqual(stable["classification_confidence"], 0.9)
        
        # Verify allowlisted extra fields
        self.assertEqual(extra.get("content_timeliness"), "current")
        self.assertEqual(extra.get("primary_evidence_type"), "radar_sensor")
        self.assertNotIn("unauthorized_key", extra)

    def test_additional_signals_invalid_enums(self) -> None:
        raw_response = {
            "topic_class": "core",
            "classification_confidence": 0.9,
            "classification_reason": "Direct reference to declassified sensor video.",
            "content_density": "high",
            "source_text_quality": "strong",
            "primary_language_code": "en",
            "governmental_involvement": 1,
            "content_timeliness": "invalid-enum-value",
            "primary_evidence_type": "radar_sensor"
        }
        with self.assertRaises(ValueError):
            validate_classification_response(raw_response)

    def test_validation_missing_required(self) -> None:
        raw_response = {
            "topic_class": "core",
            "classification_confidence": 0.9,
            "classification_reason": "Reason",
            # missing text density, quality, etc.
        }
        with self.assertRaises(ValueError):
            validate_classification_response(raw_response)


class TestDatabaseRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        
        # Seed mock Ingest tables locally to decouple tests
        create_mock_ingest_tables(self.db_path)
        # Run Classify migrations
        run_migrations(self.db_path, DEFAULT_CLASSIFY_MIGRATIONS)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_test_item(self, conn, item_id: int, title: str, text: str, text_processing_status: str = 'completed', text_processing_reason: Optional[str] = None) -> None:
        cursor = conn.cursor()
        # Seed source_item
        cursor.execute("""
            INSERT INTO source_item (
                source_item_id, source_id, title, ingest_dedup_key, dedup_rule, ingest_status, fetched_at
            ) VALUES (?, 1, ?, ?, 'guid', 'ingested', '2026-06-13T21:00:00Z')
        """, (item_id, title, f"key-{item_id}"))
        
        # Seed source_item_text
        cursor.execute("""
            INSERT INTO source_item_text (
                source_item_id, sanitized_text, sanitization_method, html_detected, was_truncated,
                text_processing_status, text_processing_reason, sanitized_text_length, created_at, updated_at
            ) VALUES (?, ?, 'clean_v1', 0, 0, ?, ?, ?, '2026-06-13T21:00:00Z', '2026-06-13T21:00:00Z')
        """, (item_id, text, text_processing_status, text_processing_reason, len(text)))
        conn.commit()

    def test_pending_query_and_upsert(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = ClassificationResultRepository(conn)

            # 1. Seed two items: one completed (pending classification), one low_context (excluded)
            self.seed_test_item(conn, 10, "First Item", "This is working text body.", text_processing_status='completed')
            self.seed_test_item(conn, 20, "Second Item", "Thin", text_processing_status='low_context', text_processing_reason='too_short')

            # 2. Get pending items (should only find Item 10)
            pending = repo.get_pending_items(limit=5)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["source_item_id"], 10)

            # 3. Write classification for item 10
            repo.upsert({
                "source_item_id": 10,
                "topic_class": "core",
                "classification_reason": "Matches UAP criteria.",
                "classification_confidence": 0.85,
                "content_density": "medium",
                "source_text_quality": "usable",
                "primary_language_code": "en",
                "governmental_involvement": 0,
                "additional_signals": {"content_timeliness": "current"},
                "model_name": "test-model",
                "prompt_version": "v1"
            })
            conn.commit()

            # 4. Get pending again (should find zero now, since 20 is excluded and 10 is classified)
            pending_after = repo.get_pending_items(limit=5)
            self.assertEqual(len(pending_after), 0)

            # 5. Test ON CONFLICT DO UPDATE upsert behaviour on item 10
            repo.upsert({
                "source_item_id": 10,
                "topic_class": "adjacent", # Update class
                "classification_reason": "Adjusted reason.",
                "classification_confidence": 0.70,
                "content_density": "low",
                "source_text_quality": "usable",
                "primary_language_code": "en",
                "governmental_involvement": 1,
                "additional_signals": None,
                "model_name": "test-model",
                "prompt_version": "v2"
            })
            conn.commit()

            # Check update values
            cursor = conn.cursor()
            cursor.execute("SELECT topic_class, governmental_involvement FROM classification_result WHERE source_item_id = 10")
            row = cursor.fetchone()
            self.assertEqual(row["topic_class"], "adjacent")
            self.assertEqual(row["governmental_involvement"], 1)
        finally:
            conn.close()

    def test_constraint_violation_rejection(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = ClassificationResultRepository(conn)
            self.seed_test_item(conn, 30, "Constraint Test", "Body")

            # Confidence > 1.0 check constraint violation
            with self.assertRaises(sqlite3.IntegrityError):
                repo.upsert({
                    "source_item_id": 30,
                    "topic_class": "core",
                    "classification_confidence": 1.5, # invalid
                    "model_name": "test",
                    "prompt_version": "v1"
                })
                conn.commit()

            # Invalid topic_class check constraint violation
            with self.assertRaises(sqlite3.IntegrityError):
                repo.upsert({
                    "source_item_id": 30,
                    "topic_class": "invalid-topic-class", # invalid
                    "model_name": "test",
                    "prompt_version": "v1"
                })
                conn.commit()
        finally:
            conn.close()

    def test_cascade_delete(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = ClassificationResultRepository(conn)
            self.seed_test_item(conn, 40, "Delete Test", "Body")
            
            # Insert result
            repo.upsert({
                "source_item_id": 40,
                "topic_class": "irrelevant",
                "model_name": "test",
                "prompt_version": "v1"
            })
            conn.commit()

            # Confirm it exists
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM classification_result WHERE source_item_id = 40")
            self.assertIsNotNone(cursor.fetchone())

            # Delete source_item_text first (since it has a restrictive delete on source_item)
            cursor.execute("DELETE FROM source_item_text WHERE source_item_id = 40")
            # Delete source_item
            cursor.execute("DELETE FROM source_item WHERE source_item_id = 40")
            conn.commit()

            # Confirm classification_result was deleted via cascade
            cursor.execute("SELECT 1 FROM classification_result WHERE source_item_id = 40")
            self.assertIsNone(cursor.fetchone())
        finally:
            conn.close()


class TestOrchestrator(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        create_mock_ingest_tables(self.db_path)
        run_migrations(self.db_path, DEFAULT_CLASSIFY_MIGRATIONS)

        # Setup mock configs
        self.config = MagicMock(spec=ClassifyConfig)
        self.config.active_provider_name = "test-provider"
        self.config.active_provider = MagicMock()
        self.config.active_provider.model_name = "gpt-5.4-mini"
        self.config.active_provider.api_base = "https://api.test.com"
        self.config.active_provider.api_key_env = "TEST_API_KEY"
        self.config.active_provider.supports_structured_output = True

        self.config.active_template = MagicMock()
        self.config.active_template.version = "v4.0"
        self.config.active_template.system_instruction = "System Instruction"
        self.config.active_template.user_prompt_template = "Title: {title}, Text: {sanitized_text}"

        self.config.execution_policy = MagicMock()
        self.config.execution_policy.batch_size = 20
        self.config.execution_policy.max_concurrent_requests = 3
        self.config.execution_policy.rate_limit_per_minute = 60
        self.config.execution_policy.request_timeout_seconds = 10.0
        self.config.execution_policy.retry_attempts = 2
        self.config.execution_policy.backoff_factor = 0.1

        self.config.request_defaults = MagicMock()
        self.config.request_defaults.temperature = 0.1
        self.config.request_defaults.top_p = 0.95
        self.config.request_defaults.max_output_tokens = 500



    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_test_item(self, item_id: int, title: str, text: str, text_processing_status: str = 'completed', text_processing_reason: Optional[str] = None) -> None:
        conn = get_connection(self.db_path)
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
            """, (item_id, text, text_processing_status, text_processing_reason, len(text)))
            conn.commit()
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_success_and_bypass(self, mock_post) -> None:
        # Seed two items: one normal, one low-context (bypass)
        self.seed_test_item(1, "Core UAP Hearing", "Active congressional committee discussed military radar tracks.", text_processing_status='completed')
        self.seed_test_item(2, "Low Context", "Thin text", text_processing_status='low_context')

        # Mock LLM API Response for item 1
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "topic_class": "core",
                        "classification_confidence": 0.95,
                        "classification_reason": "Congressional hearing UAP case.",
                        "content_density": "high",
                        "source_text_quality": "strong",
                        "primary_language_code": "en",
                        "governmental_involvement": 1,
                        "content_timeliness": "current",
                        "primary_evidence_type": "radar_sensor"
                    })
                }
            }]
        }
        mock_post.return_value = mock_response

        # Run orchestrator
        summary = asyncio.run(orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            batch_size=10
        ))

        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        # Validate database values
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            
            # Check normal LLM item (1)
            cursor.execute("SELECT * FROM classification_result WHERE source_item_id = 1")
            res1 = cursor.fetchone()
            self.assertIsNotNone(res1)
            self.assertEqual(res1["topic_class"], "core")
            self.assertEqual(res1["governmental_involvement"], 1)
            self.assertEqual(res1["model_name"], "gpt-5.4-mini")
            signals = json.loads(res1["additional_signals"])
            self.assertEqual(signals.get("primary_evidence_type"), "radar_sensor")

            # Check that low-context item (2) has no classification result
            cursor.execute("SELECT * FROM classification_result WHERE source_item_id = 2")
            res2 = cursor.fetchone()
            self.assertIsNone(res2)
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_llm_failure_isolation(self, mock_post) -> None:
        # Seed two items (both normal, requiring LLM calls)
        self.seed_test_item(100, "Core Case", "UFO reported in sky.")
        self.seed_test_item(200, "Fail Case", "Bad content.")

        # Configure mock_post to return success for item 100 and throw error for item 200
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "topic_class": "core",
                        "classification_confidence": 0.8,
                        "classification_reason": "Visual sightings.",
                        "content_density": "medium",
                        "source_text_quality": "usable",
                        "primary_language_code": "en",
                        "governmental_involvement": 0,
                        "content_timeliness": None,
                        "primary_evidence_type": None
                    })
                }
            }]
        }

        # Mock fail responses
        mock_fail = MagicMock()
        mock_fail.status_code = 500  # Will trigger HTTPStatusError and retry
        
        # Setup side effect: first call (item 100) succeeded, subsequent retries for item 200 fail
        mock_post.side_effect = [mock_ok, mock_fail, mock_fail]

        summary = asyncio.run(orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            batch_size=10
        ))

        # Check summary: 1 succeeded, 1 failed. The orchestrator must NOT crash on single item failure.
        self.assertEqual(summary["total_queried"], 2)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 1)

        # Confirm item 100 was written, item 200 remains pending (no result written)
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM classification_result WHERE source_item_id = 100")
            self.assertIsNotNone(cursor.fetchone())

            cursor.execute("SELECT 1 FROM classification_result WHERE source_item_id = 200")
            self.assertIsNone(cursor.fetchone())
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_dry_run_not_committed(self, mock_post) -> None:
        self.seed_test_item(300, "Dry Run Case", "Some content.")

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "topic_class": "adjacent",
                        "classification_confidence": 0.9,
                        "classification_reason": "Space technology.",
                        "content_density": "medium",
                        "source_text_quality": "strong",
                        "primary_language_code": "en",
                        "governmental_involvement": 0,
                        "content_timeliness": None,
                        "primary_evidence_type": None
                    })
                }
            }]
        }
        mock_post.return_value = mock_ok

        # Execute under dry run
        summary = asyncio.run(orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            batch_size=10,
            dry_run=True
        ))

        # Check summary: processed successfully
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)

        # Confirm no database updates are persisted
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM classification_result WHERE source_item_id = 300")
            self.assertIsNone(cursor.fetchone())
        finally:
            conn.close()

    def test_orchestrate_preview_prompts_summary(self) -> None:
        self.seed_test_item(400, "Preview Case 1", "Content 1.", text_processing_status='completed')
        self.seed_test_item(500, "Preview Case 2", "Content 2.", text_processing_status='low_context')

        summary = asyncio.run(orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            batch_size=10,
            preview_prompts=True
        ))

        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 0)
        self.assertEqual(summary["previewed"], 1)
        self.assertEqual(summary["status"], "preview")

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_sqlite_concurrency_safe(self, mock_post) -> None:
        # Seed 3 items to test concurrent DB write locking
        self.seed_test_item(1001, "Case 1", "Body 1")
        self.seed_test_item(1002, "Case 2", "Body 2")
        self.seed_test_item(1003, "Case 3", "Body 3")

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "topic_class": "core",
                        "classification_confidence": 0.9,
                        "classification_reason": "Visual sighting.",
                        "content_density": "low",
                        "source_text_quality": "usable",
                        "primary_language_code": "en",
                        "governmental_involvement": 0,
                        "content_timeliness": None,
                        "primary_evidence_type": None
                    })
                }
            }]
        }
        mock_post.return_value = mock_ok

        # Execute orchestrator - this spins up 3 parallel tasks on the same sqlite connection.
        # The internal db_lock should prevent any "cannot start a transaction within a transaction" errors.
        summary = asyncio.run(orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            batch_size=10
        ))

        self.assertEqual(summary["processed_successfully"], 3)
        self.assertEqual(summary["failures"], 0)

        # Confirm all 3 results were written
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) as count FROM classification_result WHERE source_item_id IN (1001, 1002, 1003)")
            self.assertEqual(cursor.fetchone()["count"], 3)
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_model_refusal_no_retries(self, mock_post) -> None:
        self.seed_test_item(2001, "Refusal Case", "Content")

        # Mock API response explicitly returning a refusal
        mock_refusal = MagicMock()
        mock_refusal.status_code = 200
        mock_refusal.json.return_value = {
            "choices": [{
                "message": {
                    "refusal": "I cannot classify this content because it violates safety guidelines."
                }
            }]
        }
        mock_post.return_value = mock_refusal

        summary = asyncio.run(orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            batch_size=10
        ))

        # Should fail the item
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 0)
        self.assertEqual(summary["failures"], 1)

        # The mock API should be called EXACTLY once (no retries!)
        self.assertEqual(mock_post.call_count, 1)

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_non_string_content_triggers_retry(self, mock_post) -> None:
        self.seed_test_item(2002, "Non String Case", "Content")

        # Mock first response returning a non-string list content (should trigger retry)
        mock_bad = MagicMock()
        mock_bad.status_code = 200
        mock_bad.json.return_value = {
            "choices": [{
                "message": {
                    "content": [{"type": "text", "text": "not-a-string"}] # invalid content type
                }
            }]
        }

        # Mock second response returning correct JSON string
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "topic_class": "irrelevant",
                        "classification_confidence": 0.8,
                        "classification_reason": "Visual.",
                        "content_density": "low",
                        "source_text_quality": "usable",
                        "primary_language_code": "en",
                        "governmental_involvement": 0,
                        "content_timeliness": None,
                        "primary_evidence_type": None
                    })
                }
            }]
        }

        mock_post.side_effect = [mock_bad, mock_ok]

        summary = asyncio.run(orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            batch_size=10
        ))

        # Should succeed on the second attempt
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        # Should be called exactly twice
        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
