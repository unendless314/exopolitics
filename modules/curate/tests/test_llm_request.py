import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from modules.curate.src.orchestrator import (
    JSON_SCHEMA,
    ModelRefusalError,
    NonRetryableHTTPError,
    _build_messages,
    _build_request_payload,
    _parse_response_content,
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


class TestBuildMessages(unittest.TestCase):
    def test_interpolates_all_prompt_variables(self):
        config = build_test_config(supports_structured_output=False)
        messages = _build_messages(config, ITEM)

        self.assertEqual(messages["system_instruction"], config.active_template.system_instruction)
        user_prompt = messages["user_prompt"]
        self.assertIn(ITEM["raw_title"], user_prompt)
        self.assertIn(ITEM["sanitized_text"], user_prompt)
        self.assertIn(ITEM["topic_class"], user_prompt)
        self.assertIn("Gov: 1", user_prompt)

    def test_missing_government_flag_defaults_to_zero(self):
        config = build_test_config(supports_structured_output=False)
        item = dict(ITEM, governmental_involvement=None)
        messages = _build_messages(config, item)
        self.assertIn("Gov: 0", messages["user_prompt"])


class TestBuildRequestPayload(unittest.TestCase):
    def test_config_derived_request_defaults(self):
        config = build_test_config(
            supports_structured_output=False,
            model_name="model-x",
            temperature=0.33,
            top_p=0.5,
            max_output_tokens=777,
        )
        payload = _build_request_payload(config, ITEM)

        self.assertEqual(payload["model"], config.active_provider.model_name)
        self.assertEqual(payload["temperature"], config.request_defaults.temperature)
        self.assertEqual(payload["top_p"], config.request_defaults.top_p)
        self.assertEqual(payload["max_tokens"], config.request_defaults.max_output_tokens)

        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][0]["content"], config.active_template.system_instruction)
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertIn(ITEM["raw_title"], payload["messages"][1]["content"])

    def test_structured_output_provider_gets_strict_json_schema(self):
        config = build_test_config(supports_structured_output=True)
        payload = _build_request_payload(config, ITEM)

        response_format = payload["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(response_format["json_schema"]["name"], "curation_result")
        self.assertIs(response_format["json_schema"]["strict"], True)
        self.assertIs(response_format["json_schema"]["schema"], JSON_SCHEMA)

        # Lock the outbound schema's required contract and strictness guards.
        self.assertEqual(
            JSON_SCHEMA["required"],
            ["curation_decision", "editor_brief", "curation_output"],
        )
        self.assertIs(JSON_SCHEMA["additionalProperties"], False)
        for section in ("curation_decision", "editor_brief", "curation_output"):
            with self.subTest(section=section):
                self.assertIs(JSON_SCHEMA["properties"][section]["additionalProperties"], False)

    def test_fallback_provider_gets_json_object(self):
        config = build_test_config(supports_structured_output=False)
        payload = _build_request_payload(config, ITEM)

        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("json_schema", payload)

    def test_top_p_omitted_when_unset(self):
        config = build_test_config(supports_structured_output=False, top_p=None)
        payload = _build_request_payload(config, ITEM)
        self.assertNotIn("top_p", payload)
        self.assertEqual(payload["temperature"], config.request_defaults.temperature)
        self.assertEqual(payload["max_tokens"], config.request_defaults.max_output_tokens)


class TestParseResponseContent(unittest.TestCase):
    def _response_with(self, json_data):
        return make_mock_http_response(status_code=200, json_data=json_data)

    def test_valid_content_parsed(self):
        response_data = make_valid_response("reject_discard")
        parsed = _parse_response_content(self._response_with(make_chat_completion_payload(response_data)))
        self.assertEqual(parsed, response_data)

    def test_missing_choices_rejected(self):
        with self.assertRaises(ValueError):
            _parse_response_content(self._response_with({}))

    def test_empty_choices_rejected(self):
        with self.assertRaises(ValueError):
            _parse_response_content(self._response_with({"choices": []}))

    def test_model_refusal_raises_dedicated_error(self):
        payload = {"choices": [{"message": {"refusal": "cannot curate this item"}}]}
        with self.assertRaises(ModelRefusalError):
            _parse_response_content(self._response_with(payload))

    def test_missing_null_nonstring_or_blank_content_rejected(self):
        bad_messages = [
            {},
            {"content": None},
            {"content": 123},
            {"content": "   "},
        ]
        for message in bad_messages:
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    _parse_response_content(self._response_with({"choices": [{"message": message}]}))

    def test_invalid_json_content_rejected(self):
        payload = {"choices": [{"message": {"content": "{not valid json"}}]}
        with self.assertRaises(json.JSONDecodeError):
            _parse_response_content(self._response_with(payload))

    def test_malformed_envelope_shapes_rejected_with_valueerror(self):
        # Malformed provider envelopes must surface as ValueError so the
        # retry policy treats them as retryable parsing failures; leaking
        # AttributeError/TypeError/KeyError would bypass the retry handler.
        bad_payloads = [
            {"choices": [{"message": None}]},       # null message
            {"choices": [{"message": "content"}]},  # message not an object
            {"choices": "not-a-list"},              # choices not a list
            {"choices": ["not-a-dict"]},            # choice entry not an object
            ["not-a-dict"],                         # top-level body not an object
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    _parse_response_content(self._response_with(payload))


class TestFetchLlmCurationHttpStatusHandling(unittest.TestCase):
    """Retry-eligibility contract for HTTP statuses.

    General 4xx statuses must fail fast with a single request; 429/5xx stay
    retryable up to the configured retry limit.
    """

    def _run_fetch(self, config, client):
        return asyncio.run(
            fetch_llm_curation(client=client, config=config, item=ITEM, api_key="dummy")
        )

    def test_general_4xx_makes_single_request_and_fails_fast(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                config = build_test_config(
                    supports_structured_output=False, retry_attempts=3, backoff_factor=0.1
                )
                client = FakeHTTPClient([
                    make_mock_http_response(status_code=status, text=f"error body {status}")
                ])
                with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
                    with self.assertRaises(NonRetryableHTTPError) as ctx:
                        self._run_fetch(config, client)

                self.assertEqual(ctx.exception.status_code, status)
                self.assertIn(str(status), str(ctx.exception))
                self.assertEqual(len(client.calls), 1)
                mock_sleep.assert_not_called()

    def test_retryable_429_exhausts_retry_limit(self):
        config = build_test_config(
            supports_structured_output=False, retry_attempts=3, backoff_factor=0.1
        )
        client = FakeHTTPClient([make_mock_http_response(status_code=429, text="rate limited")])
        with patch("asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                self._run_fetch(config, client)

        self.assertEqual(len(client.calls), config.execution_policy.retry_attempts)

    def test_valid_response_returned(self):
        config = build_test_config(supports_structured_output=False)
        response_data = make_valid_response("reject_discard")
        client = FakeHTTPClient([
            make_mock_http_response(status_code=200, json_data=make_chat_completion_payload(response_data))
        ])
        parsed = self._run_fetch(config, client)
        self.assertEqual(parsed, response_data)
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
