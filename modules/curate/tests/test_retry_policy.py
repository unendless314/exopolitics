import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from modules.curate.src.orchestrator import (
    ModelRefusalError,
    fetch_llm_curation,
)
from modules.curate.tests.support import (
    FakeHTTPClient,
    build_test_config,
    make_chat_completion_payload,
    make_mock_http_response,
    make_valid_response,
)

ITEM = {
    "source_item_id": 1,
    "raw_title": "RAW TITLE HERE",
    "sanitized_text": "SANITIZED BODY HERE",
    "canonical_url": "https://example.com/item",
    "topic_class": "core",
    "classification_reason": "seeded reason",
    "governmental_involvement": 1,
}


def _broken_schema_envelope():
    """A 200 response whose curation payload fails schema validation."""
    broken = make_valid_response("reject_discard")
    del broken["editor_brief"]
    return make_mock_http_response(status_code=200, json_data=make_chat_completion_payload(broken))


class TestFetchLlmCurationRetryPolicy(unittest.TestCase):
    """Retry-eligibility matrix for fetch_llm_curation.

    Retryable outcomes must consume exactly `retry_attempts` requests and
    then raise RuntimeError; non-retryable outcomes must stop after one
    request. All sleeps and jitter are mocked, so no real waiting occurs.
    """

    def _run_fetch(self, config, client):
        return asyncio.run(
            fetch_llm_curation(client=client, config=config, item=ITEM, api_key="dummy")
        )

    def test_retryable_outcomes_exhaust_retry_limit(self):
        retryable_outcomes = {
            "http_500": make_mock_http_response(status_code=500, text="server error"),
            "http_503": make_mock_http_response(status_code=503, text="overloaded"),
            "timeout": httpx.TimeoutException("request timed out"),
            "network_error": httpx.ConnectError("connection refused"),
            "invalid_json": make_mock_http_response(
                status_code=200,
                json_data={"choices": [{"message": {"content": "{not valid json"}}]},
            ),
            "empty_choices": make_mock_http_response(status_code=200, json_data={}),
            "malformed_envelope": make_mock_http_response(
                status_code=200, json_data={"choices": [{"message": None}]}
            ),
            # Decided policy: schema validation failure is retryable because
            # sampling temperature may produce a valid payload on retry.
            "schema_validation_failure": _broken_schema_envelope(),
        }
        for name, outcome in retryable_outcomes.items():
            with self.subTest(outcome=name):
                config = build_test_config(
                    supports_structured_output=False, retry_attempts=3, backoff_factor=0.1
                )
                client = FakeHTTPClient([outcome])
                with patch("asyncio.sleep", new=AsyncMock()):
                    with self.assertRaises(RuntimeError) as ctx:
                        self._run_fetch(config, client)

                attempts = config.execution_policy.retry_attempts
                self.assertEqual(len(client.calls), attempts)
                self.assertIn(f"after {attempts} attempts", str(ctx.exception))

    def test_model_refusal_not_retried(self):
        config = build_test_config(
            supports_structured_output=False, retry_attempts=3, backoff_factor=0.1
        )
        client = FakeHTTPClient([
            make_mock_http_response(
                status_code=200,
                json_data={"choices": [{"message": {"refusal": "cannot curate this"}}]},
            )
        ])
        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with self.assertRaises(ModelRefusalError):
                self._run_fetch(config, client)

        self.assertEqual(len(client.calls), 1)
        mock_sleep.assert_not_called()

    def test_transient_failure_recovers_on_later_attempt(self):
        config = build_test_config(
            supports_structured_output=False, retry_attempts=3, backoff_factor=0.1
        )
        response_data = make_valid_response("reject_discard")
        client = FakeHTTPClient([
            make_mock_http_response(status_code=503, text="overloaded"),
            make_mock_http_response(
                status_code=200, json_data=make_chat_completion_payload(response_data)
            ),
        ])
        with patch("asyncio.sleep", new=AsyncMock()):
            parsed = self._run_fetch(config, client)

        self.assertEqual(parsed, response_data)
        self.assertEqual(len(client.calls), 2)

    def test_backoff_delays_derived_from_config(self):
        config = build_test_config(
            supports_structured_output=False, retry_attempts=3, backoff_factor=2.0
        )
        client = FakeHTTPClient([make_mock_http_response(status_code=503, text="overloaded")])
        fixed_jitter = 0.5
        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep, patch(
            "random.uniform", return_value=fixed_jitter
        ):
            with self.assertRaises(RuntimeError):
                self._run_fetch(config, client)

        backoff = config.execution_policy.backoff_factor
        attempts = config.execution_policy.retry_attempts
        expected = [
            (backoff ** attempt) + fixed_jitter for attempt in range(1, attempts)
        ]
        actual = [call.args[0] for call in mock_sleep.await_args_list]
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
