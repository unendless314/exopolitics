"""Deterministic tests for retry, rate-limit, and concurrency execution rules.

These tests lock the observable behavior required by EXECUTION_POLICY.md:
no real network and no real waiting — the HTTP client is injected or mocked,
and asyncio.sleep / random jitter are patched so backoff is asserted without
wall-clock time.
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx

from modules.classify.src.database import ClassificationResultRepository, get_connection
from modules.classify.src.orchestrator import (
    ModelRefusalError,
    NonRetryableHTTPError,
    fetch_llm_classification,
    orchestrate_run,
)
from modules.classify.tests.helpers import (
    make_completion_response,
    make_config,
    make_http_response,
    seed_source_item,
    temp_classify_db,
    valid_llm_response,
)

PATCH_SLEEP = "modules.classify.src.orchestrator.asyncio.sleep"
PATCH_JITTER = "modules.classify.src.orchestrator.random.uniform"


def make_client(*side_effects) -> MagicMock:
    client = MagicMock()
    client.post = AsyncMock(side_effect=list(side_effects))
    return client


def run_fetch(config, client):
    return asyncio.run(fetch_llm_classification(
        client=client, config=config, title="t", sanitized_text="x", api_key="k"
    ))


class TestRetryEligibility(unittest.TestCase):
    def test_retries_429_up_to_configured_limit(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=2.0)
        client = make_client(*[make_http_response(429, {"error": {"message": "slow down"}})] * 3)

        with patch(PATCH_SLEEP, new=AsyncMock()) as mock_sleep, patch(PATCH_JITTER, return_value=0.5):
            with self.assertRaises(RuntimeError):
                run_fetch(config, client)

        policy = config.execution_policy
        self.assertEqual(client.post.call_count, policy.retry_attempts)
        # Backoff sleeps are derived from the config under test, plus jitter.
        expected_sleeps = [
            call(policy.backoff_factor ** attempt + 0.5)
            for attempt in range(1, policy.retry_attempts)
        ]
        self.assertEqual(mock_sleep.call_args_list, expected_sleeps)

    def test_retries_timeout_errors(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        client = make_client(*[httpx.TimeoutException("timed out")] * 3)

        with patch(PATCH_SLEEP, new=AsyncMock()) as mock_sleep, patch(PATCH_JITTER, return_value=0.5):
            with self.assertRaises(RuntimeError):
                run_fetch(config, client)

        self.assertEqual(client.post.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_retries_connection_errors(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        client = make_client(*[httpx.ConnectError("connection refused")] * 3)

        with patch(PATCH_SLEEP, new=AsyncMock()), patch(PATCH_JITTER, return_value=0.5):
            with self.assertRaises(RuntimeError):
                run_fetch(config, client)

        self.assertEqual(client.post.call_count, 3)

    def test_retries_5xx_and_recovers(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        client = make_client(
            make_http_response(500, {"error": {"message": "server error"}}),
            make_completion_response(valid_llm_response()),
        )

        with patch(PATCH_SLEEP, new=AsyncMock()) as mock_sleep, patch(PATCH_JITTER, return_value=0.5):
            stable, _ = run_fetch(config, client)

        self.assertEqual(stable["topic_class"], "core")
        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)

    def test_non_retryable_4xx_fails_after_single_attempt(self) -> None:
        # General 4xx (400/401/403) are not retry-eligible per EXECUTION_POLICY.
        for status in (400, 401, 403):
            with self.subTest(status=status):
                config = make_config(retry_attempts=3, backoff_factor=0.01)
                client = make_client(make_http_response(status, {"error": {"message": "client error"}}))

                with patch(PATCH_SLEEP, new=AsyncMock()) as mock_sleep, patch(PATCH_JITTER, return_value=0.5):
                    with self.assertRaises(NonRetryableHTTPError):
                        run_fetch(config, client)

                self.assertEqual(client.post.call_count, 1)
                mock_sleep.assert_not_called()

    def test_invalid_json_content_retried_then_fails(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        bad = make_http_response(200, {"choices": [{"message": {"content": "this is not json"}}]})
        client = make_client(*[bad] * 3)

        with patch(PATCH_SLEEP, new=AsyncMock()), patch(PATCH_JITTER, return_value=0.5):
            with self.assertRaises(RuntimeError):
                run_fetch(config, client)

        self.assertEqual(client.post.call_count, 3)

    def test_invalid_json_content_retried_then_recovers(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        bad = make_http_response(200, {"choices": [{"message": {"content": "this is not json"}}]})
        client = make_client(bad, make_completion_response(valid_llm_response()))

        with patch(PATCH_SLEEP, new=AsyncMock()), patch(PATCH_JITTER, return_value=0.5):
            stable, _ = run_fetch(config, client)

        self.assertEqual(stable["topic_class"], "core")
        self.assertEqual(client.post.call_count, 2)

    def test_empty_choices_retried(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        client = make_client(*[make_http_response(200, {"choices": []})] * 3)

        with patch(PATCH_SLEEP, new=AsyncMock()), patch(PATCH_JITTER, return_value=0.5):
            with self.assertRaises(RuntimeError):
                run_fetch(config, client)

        self.assertEqual(client.post.call_count, 3)

    def test_schema_validation_failure_retried(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        bad = make_completion_response(valid_llm_response(topic_class="not-a-topic"))
        client = make_client(*[bad] * 3)

        with patch(PATCH_SLEEP, new=AsyncMock()), patch(PATCH_JITTER, return_value=0.5):
            with self.assertRaises(RuntimeError):
                run_fetch(config, client)

        self.assertEqual(client.post.call_count, 3)

    def test_model_refusal_not_retried(self) -> None:
        config = make_config(retry_attempts=3, backoff_factor=0.01)
        refusal = make_http_response(200, {"choices": [{"message": {"refusal": "cannot classify this"}}]})
        client = make_client(refusal)

        with patch(PATCH_SLEEP, new=AsyncMock()) as mock_sleep, patch(PATCH_JITTER, return_value=0.5):
            with self.assertRaises(ModelRefusalError):
                run_fetch(config, client)

        self.assertEqual(client.post.call_count, 1)
        mock_sleep.assert_not_called()

    def test_backoff_schedule_derives_from_config(self) -> None:
        config = make_config(retry_attempts=4, backoff_factor=3.0)
        client = make_client(*[make_http_response(500, {"error": {"message": "server error"}})] * 4)

        with patch(PATCH_SLEEP, new=AsyncMock()) as mock_sleep, patch(PATCH_JITTER, return_value=0.25):
            with self.assertRaises(RuntimeError):
                run_fetch(config, client)

        policy = config.execution_policy
        self.assertEqual(client.post.call_count, policy.retry_attempts)
        expected_sleeps = [
            call(policy.backoff_factor ** attempt + 0.25)
            for attempt in range(1, policy.retry_attempts)
        ]
        self.assertEqual(mock_sleep.call_args_list, expected_sleeps)


class TestFailureIsolation(unittest.TestCase):
    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_failed_item_not_written_and_remains_pending(self, mock_post) -> None:
        with temp_classify_db() as db_path:
            seed_source_item(db_path, 1, "Always Fails", "Body text.")
            config = make_config(retry_attempts=2, backoff_factor=0.01)
            mock_post.return_value = make_http_response(500, {"error": {"message": "server error"}})

            with patch(PATCH_SLEEP, new=AsyncMock()), patch(PATCH_JITTER, return_value=0.1):
                summary = asyncio.run(orchestrate_run(config=config, db_path=db_path, batch_size=10))

            self.assertEqual(summary["failures"], 1)
            self.assertEqual(summary["processed_successfully"], 0)

            conn = get_connection(db_path)
            try:
                repo = ClassificationResultRepository(conn)
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM classification_result WHERE source_item_id = 1")
                self.assertIsNone(cursor.fetchone())
                # The failed item stays eligible for the next run.
                pending_ids = {row["source_item_id"] for row in repo.get_pending_items(limit=10)}
                self.assertIn(1, pending_ids)
            finally:
                conn.close()


class TestRateLimitSchedule(unittest.TestCase):
    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_rate_limit_stagger_derives_from_execution_policy(self, mock_post) -> None:
        with temp_classify_db() as db_path:
            for idx in range(1, 4):
                seed_source_item(db_path, idx, f"Case {idx}", f"Body {idx}")
            config = make_config(rate_limit_per_minute=600)
            mock_post.return_value = make_completion_response(valid_llm_response())

            with patch(PATCH_SLEEP, new=AsyncMock()) as mock_sleep:
                summary = asyncio.run(orchestrate_run(config=config, db_path=db_path, batch_size=10))

            self.assertEqual(summary["processed_successfully"], 3)
            # Worker idx sleeps idx * (60 / rpm) before its request; idx 0 does not sleep.
            rpm = config.execution_policy.rate_limit_per_minute
            expected_sleeps = [call(idx * (60.0 / rpm)) for idx in (1, 2)]
            self.assertEqual(mock_sleep.call_args_list, expected_sleeps)


class TestSemaphoreConcurrency(unittest.TestCase):
    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_in_flight_requests_never_exceed_limit(self, mock_post) -> None:
        with temp_classify_db() as db_path:
            for idx in range(1, 7):
                seed_source_item(db_path, idx, f"Case {idx}", f"Body {idx}")
            # Huge rpm collapses the rate-limit stagger so this test isolates
            # the semaphore; asyncio.sleep is intentionally NOT patched here
            # so tasks really interleave inside the fake request.
            config = make_config(max_concurrent_requests=3, rate_limit_per_minute=1_000_000_000)

            state = {"in_flight": 0, "max_in_flight": 0}

            async def track(*args, **kwargs):
                state["in_flight"] += 1
                state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
                try:
                    for _ in range(3):
                        await asyncio.sleep(0)
                    return make_completion_response(valid_llm_response())
                finally:
                    state["in_flight"] -= 1

            mock_post.side_effect = track
            summary = asyncio.run(orchestrate_run(config=config, db_path=db_path, batch_size=10))

            self.assertEqual(summary["processed_successfully"], 6)
            limit = config.execution_policy.max_concurrent_requests
            self.assertLessEqual(state["max_in_flight"], limit)
            self.assertEqual(state["max_in_flight"], limit)


if __name__ == "__main__":
    unittest.main()
