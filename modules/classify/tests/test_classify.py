import asyncio
import contextlib
import io
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from modules.classify.src.config import validate_and_load_config
from modules.classify.src.database import (
    run_migrations,
    get_connection,
    ClassificationResultRepository,
)
from modules.classify.src.orchestrator import (
    orchestrate_run,
    validate_classification_response,
)
from modules.classify.tests.helpers import (
    CLASSIFY_MIGRATIONS_DIR,
    create_mock_ingest_tables,
    make_config,
    make_http_response,
    seed_source_item,
)

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
        run_migrations(self.db_path, CLASSIFY_MIGRATIONS_DIR)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_pending_query_and_upsert(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = ClassificationResultRepository(conn)

            # 1. Seed the queue-eligibility matrix: every outcome except
            # post_cleanup_empty and failed enters the pending queue
            seed_source_item(self.db_path, 10, "Completed Item", "This is working text body.", status='completed')
            seed_source_item(self.db_path, 20, "Mostly Links", "https://example.com/a https://example.com/b", status='low_context', reason='mostly_links')
            seed_source_item(self.db_path, 21, "Too Short", "Thin", status='low_context', reason='too_short')
            seed_source_item(self.db_path, 22, "Title Heavy", "Title-like text", status='low_context', reason='title_heavy')
            seed_source_item(self.db_path, 23, "Title Only", "Title only text", status='low_context', reason='title_only')
            seed_source_item(self.db_path, 24, "Template Heavy", "Boilerplate text", status='low_context', reason='template_heavy')
            seed_source_item(self.db_path, 25, "Truncated", "Truncated text", status='low_context', reason='truncated_to_low_context')
            seed_source_item(self.db_path, 26, "Empty After Cleanup", "", status='low_context', reason='post_cleanup_empty')
            seed_source_item(self.db_path, 27, "Missing Body", "", status='failed', reason='missing_body')
            seed_source_item(self.db_path, 28, "Sanitizer Exception", "", status='failed', reason='sanitizer_exception')

            # 2. Get pending items: completed plus every allowed low-context reason
            pending = repo.get_pending_items(limit=20)
            pending_ids = {row["source_item_id"] for row in pending}
            self.assertEqual(pending_ids, {10, 20, 21, 22, 23, 24, 25})

            # Pending rows expose only the prompt inputs, never status/reason metadata
            for row in pending:
                self.assertEqual(set(row.keys()), {"source_item_id", "title", "sanitized_text"})

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

            # 4. Item 10 leaves the pending queue once a classification_result exists
            pending_after = repo.get_pending_items(limit=20)
            self.assertEqual({row["source_item_id"] for row in pending_after}, {20, 21, 22, 23, 24, 25})

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
            seed_source_item(self.db_path, 30, "Constraint Test", "Body")

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
            seed_source_item(self.db_path, 40, "Delete Test", "Body")
            
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
        run_migrations(self.db_path, CLASSIFY_MIGRATIONS_DIR)

        # Real config object with explicit, test-controlled execution policy
        self.config = make_config(
            supports_structured_output=True,
            request_timeout_seconds=10.0,
            retry_attempts=2,
            backoff_factor=0.1,
            max_output_tokens=500,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_success_and_exclusions(self, mock_post) -> None:
        # Seed one completed item and one allowed low-context item (both proceed
        # through the normal LLM path), plus the two excluded outcomes
        seed_source_item(self.db_path,1, "Core UAP Hearing", "Active congressional committee discussed military radar tracks.", status='completed')
        seed_source_item(self.db_path,2, "Link Wrapper", "https://news.example/a https://news.example/b", status='low_context', reason='mostly_links')
        seed_source_item(self.db_path,3, "Empty After Cleanup", "", status='low_context', reason='post_cleanup_empty')
        seed_source_item(self.db_path,4, "Missing Body", "", status='failed', reason='missing_body')

        # Mock LLM API Response for every eligible item
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

        # Items 1 and 2 are queried and classified; excluded items never invoke the model
        self.assertEqual(summary["total_queried"], 2)
        self.assertEqual(summary["processed_successfully"], 2)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(mock_post.call_count, 2)

        # Prompt payloads carry only title and sanitized text, never status/reason metadata
        user_contents = set()
        for call in mock_post.call_args_list:
            payload = call.kwargs["json"]
            user_contents.add(payload["messages"][1]["content"])
        self.assertEqual(user_contents, {
            "Title: Core UAP Hearing, Text: Active congressional committee discussed military radar tracks.",
            "Title: Link Wrapper, Text: https://news.example/a https://news.example/b",
        })
        for content in user_contents:
            self.assertNotIn("low_context", content)
            self.assertNotIn("mostly_links", content)

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

            # Check that the allowed low-context item (2) was classified too
            cursor.execute("SELECT * FROM classification_result WHERE source_item_id = 2")
            self.assertIsNotNone(cursor.fetchone())

            # Check that the excluded outcomes (3 and 4) have no classification result
            cursor.execute("SELECT * FROM classification_result WHERE source_item_id = 3")
            self.assertIsNone(cursor.fetchone())
            cursor.execute("SELECT * FROM classification_result WHERE source_item_id = 4")
            self.assertIsNone(cursor.fetchone())
        finally:
            conn.close()

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_orchestrate_llm_failure_isolation(self, mock_post) -> None:
        # Seed two items (both normal, requiring LLM calls)
        seed_source_item(self.db_path,100, "Core Case", "UFO reported in sky.")
        seed_source_item(self.db_path,200, "Fail Case", "Bad content.")

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

        # Mock fail responses: a real 500 response keeps raise_for_status
        # behavior, so the item exercises the retry path (retry_attempts=2).
        mock_fail = make_http_response(500, {"error": {"message": "server error"}})

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
        seed_source_item(self.db_path,300, "Dry Run Case", "Some content.")

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
        seed_source_item(self.db_path,400, "Preview Case 1", "Content 1.", status='completed')
        seed_source_item(self.db_path,500, "Preview Case 2", "Content 2.", status='low_context', reason='too_short')
        seed_source_item(self.db_path,600, "Preview Case 3", "", status='low_context', reason='post_cleanup_empty')
        seed_source_item(self.db_path,700, "Preview Case 4", "", status='failed', reason='sanitizer_exception')

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            summary = asyncio.run(orchestrate_run(
                config=self.config,
                db_path=self.db_path,
                batch_size=10,
                preview_prompts=True
            ))

        # Allowed items (completed + low_context) are previewed; excluded outcomes are not
        self.assertEqual(summary["total_queried"], 2)
        self.assertEqual(summary["processed_successfully"], 0)
        self.assertEqual(summary["previewed"], 2)
        self.assertEqual(summary["status"], "preview")

        preview_output = stdout.getvalue()
        self.assertIn("Source Item ID: 400", preview_output)
        self.assertIn("Source Item ID: 500", preview_output)
        self.assertNotIn("Source Item ID: 600", preview_output)
        self.assertNotIn("Source Item ID: 700", preview_output)

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_sqlite_concurrency_safe(self, mock_post) -> None:
        # Seed 3 items to test concurrent DB write locking
        seed_source_item(self.db_path,1001, "Case 1", "Body 1")
        seed_source_item(self.db_path,1002, "Case 2", "Body 2")
        seed_source_item(self.db_path,1003, "Case 3", "Body 3")

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
        seed_source_item(self.db_path,2001, "Refusal Case", "Content")

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
        seed_source_item(self.db_path,2002, "Non String Case", "Content")

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
