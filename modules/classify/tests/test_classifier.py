import asyncio
import unittest
from unittest.mock import patch, AsyncMock
import tempfile
import pathlib
import sqlite3
import httpx
import logging

from modules.classify.src.config import ClassifyConfig, RequestDefaults, ExecutionPolicy, ProviderConfig, DeterministicClassification
from modules.classify.src.prompt_loader import PromptTemplate
from modules.classify.src.repository import get_connection, run_migrations, ClassificationRepository
from modules.classify.src.classifier import process_single_item, classify_batch, parse_and_validate_response

# Disable logging outputs during testing to keep test console clean
logging.disable(logging.CRITICAL)

class TestClassifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_classifier.db"
        
        # Paths to migrations
        self.ingest_migrations_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "ingest" / "src" / "migrations"
        self.classify_migrations_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

        # Apply migrations
        run_migrations(self.db_path, self.ingest_migrations_dir)
        run_migrations(self.db_path, self.classify_migrations_dir)
        
        self.conn = get_connection(self.db_path)
        self.repo = ClassificationRepository(self.conn)

        # Setup standard mock configs
        self.config = ClassifyConfig(
            active_provider="openai",
            active_prompt_template="single_item_v2",
            request_defaults=RequestDefaults(temperature=0.1, top_p=0.95, max_output_tokens=1024),
            execution_policy=ExecutionPolicy(
                batch_size=20,
                max_concurrent_requests=3,
                rate_limit_per_minute=6000, # Large limit for tests
                request_timeout_seconds=2.0,
                min_context_characters=100,
                retry_attempts=3,
                backoff_factor=0.001 # Fast retries for testing
            ),
            providers={
                "openai": ProviderConfig(
                    api_type="openai",
                    api_key_env="OPENAI_API_KEY",
                    model_name="gpt-5.4-mini",
                    supports_structured_output=True
                )
            },
            deterministic_classification=DeterministicClassification(
                model_name="deterministic-low-context",
                prompt_version="rule_v1"
            )
        )

        self.template = PromptTemplate(
            name="single_item_v2",
            version="v2.0",
            description="Test Prompt",
            system_instruction="You are a classifier.",
            user_prompt_template="Title: {title} Summary: {summary}"
        )

        # Environment variable mock for API key
        self.env_patcher = patch.dict("os.environ", {"OPENAI_API_KEY": "test-key-value"})
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()
        self.env_patcher.stop()

    def _insert_test_item(self, source_item_id: int, title: str, summary: str) -> dict:
        self.conn.execute(
            """
            INSERT INTO source_item (
                source_item_id, source_id, title, summary, fetched_at, 
                ingest_dedup_key, dedup_rule, ingest_status, created_at
            ) VALUES (?, ?, ?, ?, '2026-06-05T00:00:00Z', ?, 'guid', 'ingested', '2026-06-05T00:00:00Z')
            """,
            (source_item_id, 1, title, summary, f"key_{source_item_id}")
        )
        self.conn.commit()
        return {
            "source_item_id": source_item_id,
            "title": title,
            "summary": summary
        }

    @patch("httpx.AsyncClient.post")
    async def test_tc01_deterministic_low_context(self, mock_post) -> None:
        # Title + Summary length is 30 < 100
        item = self._insert_test_item(101, "Low Context", "Thin summary")
        
        progress = lambda *args: None
        sem = asyncio.Semaphore(1)
        from modules.classify.src.classifier import AsyncRateLimiter
        rl = AsyncRateLimiter(60)

        async with httpx.AsyncClient() as client:
            result = await process_single_item(
                item=item,
                client=client,
                config=self.config,
                provider_config=self.config.providers["openai"],
                template=self.template,
                repo=self.repo,
                sem=sem,
                rate_limiter=rl,
                progress_callback=progress
            )

        # 1. Bypasses LLM (mock_post not called)
        mock_post.assert_not_called()

        # 2. Instantly writes expected DB fields
        self.assertIsNotNone(result)
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM classification_result WHERE source_item_id = 101")
        row = dict(cursor.fetchone())
        
        self.assertEqual(row["topic_class"], "unknown")
        self.assertIsNone(row["classification_confidence"])
        self.assertEqual(row["edit_candidate"], 0)
        self.assertIn("below the minimum context threshold", row["classification_reason"])
        self.assertEqual(row["model_name"], "deterministic-low-context")
        self.assertEqual(row["prompt_version"], "rule_v1")

    @patch("httpx.AsyncClient.post")
    async def test_tc02_successful_core_classification(self, mock_post) -> None:
        title = "UAP military encounter over Pacific airspace"
        summary = "An official sensor footage analysis of a navy pilot sighting of anomalous aerial phenomena moving at high speeds without thermal signature. Sighted in May 2026."
        item = self._insert_test_item(102, title, summary)

        # Mock LLM successful JSON output
        mock_response = httpx.Response(200, json={
            "choices": [{
                "message": {
                    "content": '{"topic_class": "core", "classification_confidence": 0.97, "edit_candidate": 1, "classification_reason": "Direct military sensor encounter of anomalous objects."}'
                }
            }]
        })
        mock_post.return_value = mock_response

        progress = lambda *args: None
        sem = asyncio.Semaphore(1)
        from modules.classify.src.classifier import AsyncRateLimiter
        rl = AsyncRateLimiter(60)

        async with httpx.AsyncClient() as client:
            result = await process_single_item(
                item=item,
                client=client,
                config=self.config,
                provider_config=self.config.providers["openai"],
                template=self.template,
                repo=self.repo,
                sem=sem,
                rate_limiter=rl,
                progress_callback=progress
            )

        self.assertIsNotNone(result)
        mock_post.assert_called_once()

        # Verify DB output
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM classification_result WHERE source_item_id = 102")
        row = dict(cursor.fetchone())

        self.assertEqual(row["topic_class"], "core")
        self.assertEqual(row["classification_confidence"], 0.97)
        self.assertEqual(row["edit_candidate"], 1)
        self.assertEqual(row["classification_reason"], "Direct military sensor encounter of anomalous objects.")
        self.assertEqual(row["model_name"], "gpt-5.4-mini")
        self.assertEqual(row["prompt_version"], "v2.0")

    @patch("httpx.AsyncClient.post")
    async def test_tc03_llm_produced_unknown(self, mock_post) -> None:
        title = "Very mysterious incident in the sky"
        summary = "We saw something that we can't explain but it might be anything, maybe a bird or a plane, it was quite unclear and nobody knows what actually happened next."
        item = self._insert_test_item(103, title, summary)

        # Mock LLM unknown JSON output
        mock_response = httpx.Response(200, json={
            "choices": [{
                "message": {
                    "content": '{"topic_class": "unknown", "classification_confidence": 0.50, "edit_candidate": 0, "classification_reason": "Extremely vague text with no factual info."}'
                }
            }]
        })
        mock_post.return_value = mock_response

        progress = lambda *args: None
        sem = asyncio.Semaphore(1)
        from modules.classify.src.classifier import AsyncRateLimiter
        rl = AsyncRateLimiter(60)

        async with httpx.AsyncClient() as client:
            result = await process_single_item(
                item=item,
                client=client,
                config=self.config,
                provider_config=self.config.providers["openai"],
                template=self.template,
                repo=self.repo,
                sem=sem,
                rate_limiter=rl,
                progress_callback=progress
            )

        self.assertIsNotNone(result)
        cursor = self.conn.cursor()
        cursor.execute("SELECT topic_class, classification_confidence FROM classification_result WHERE source_item_id = 103")
        row = cursor.fetchone()
        self.assertEqual(row["topic_class"], "unknown")
        self.assertEqual(row["classification_confidence"], 0.50)

    @patch("httpx.AsyncClient.post")
    async def test_tc04_transient_malformed_output(self, mock_post) -> None:
        title = "UAP military encounter over Pacific airspace"
        summary = "An official sensor footage analysis of a navy pilot sighting of anomalous aerial phenomena moving at high speeds without thermal signature. Sighted in May 2026."
        item = self._insert_test_item(104, title, summary)

        # Mock first call returns malformed text, second call returns valid json
        mock_post.side_effect = [
            httpx.Response(200, json={
                "choices": [{
                    "message": {
                        "content": "This is not json at all."
                    }
                }]
            }),
            httpx.Response(200, json={
                "choices": [{
                    "message": {
                        "content": '{"topic_class": "core", "classification_confidence": 0.90, "edit_candidate": 0, "classification_reason": "Resolved encounter."}'
                    }
                }]
            })
        ]

        progress = lambda *args: None
        sem = asyncio.Semaphore(1)
        from modules.classify.src.classifier import AsyncRateLimiter
        rl = AsyncRateLimiter(60)

        async with httpx.AsyncClient() as client:
            result = await process_single_item(
                item=item,
                client=client,
                config=self.config,
                provider_config=self.config.providers["openai"],
                template=self.template,
                repo=self.repo,
                sem=sem,
                rate_limiter=rl,
                progress_callback=progress
            )

        self.assertIsNotNone(result)
        self.assertEqual(mock_post.call_count, 2)
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT topic_class FROM classification_result WHERE source_item_id = 104")
        self.assertEqual(cursor.fetchone()[0], "core")

    @patch("httpx.AsyncClient.post")
    async def test_tc05_persistent_failure(self, mock_post) -> None:
        title = "UAP military encounter over Pacific airspace"
        summary = "An official sensor footage analysis of a navy pilot sighting of anomalous aerial phenomena moving at high speeds without thermal signature. Sighted in May 2026."
        item = self._insert_test_item(105, title, summary)

        # Mock consistently returns malformed text
        mock_post.return_value = httpx.Response(200, json={
            "choices": [{
                "message": {
                    "content": "Not JSON at all."
                }
            }]
        })

        progress = lambda *args: None
        sem = asyncio.Semaphore(1)
        from modules.classify.src.classifier import AsyncRateLimiter
        rl = AsyncRateLimiter(60)

        async with httpx.AsyncClient() as client:
            result = await process_single_item(
                item=item,
                client=client,
                config=self.config,
                provider_config=self.config.providers["openai"],
                template=self.template,
                repo=self.repo,
                sem=sem,
                rate_limiter=rl,
                progress_callback=progress
            )

        # Writes NO row for this item
        self.assertIsNone(result)
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM classification_result WHERE source_item_id = 105")
        self.assertEqual(cursor.fetchone()[0], 0)

    def test_response_validation_rules(self) -> None:
        # Test mandatory keys check
        with self.assertRaises(ValueError) as context:
            parse_and_validate_response('{"topic_class": "core"}')
        self.assertIn("Missing mandatory key", str(context.exception))

        # Test invalid topic class
        with self.assertRaises(ValueError) as context:
            parse_and_validate_response('{"topic_class": "invalid_topic", "classification_confidence": 0.8, "edit_candidate": 0, "classification_reason": "abc"}')
        self.assertIn("Invalid topic_class", str(context.exception))

        # Test confidence out of bounds
        with self.assertRaises(ValueError) as context:
            parse_and_validate_response('{"topic_class": "core", "classification_confidence": 1.2, "edit_candidate": 0, "classification_reason": "abc"}')
        self.assertIn("classification_confidence must be between 0.0 and 1.0", str(context.exception))

        # Test confidence not a number
        with self.assertRaises(ValueError) as context:
            parse_and_validate_response('{"topic_class": "core", "classification_confidence": "high", "edit_candidate": 0, "classification_reason": "abc"}')
        self.assertIn("classification_confidence must be a numeric value", str(context.exception))

        # Test valid variations of edit flag mapping
        res = parse_and_validate_response('{"topic_class": "core", "classification_confidence": 0.9, "edit_candidate": "true", "classification_reason": "abc"}')
        self.assertEqual(res["edit_candidate"], 1)

        res = parse_and_validate_response('{"topic_class": "core", "classification_confidence": 0.9, "edit_candidate": false, "classification_reason": "abc"}')
        self.assertEqual(res["edit_candidate"], 0)

if __name__ == "__main__":
    unittest.main()
