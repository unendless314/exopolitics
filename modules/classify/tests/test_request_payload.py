"""Direct unit tests for _build_messages() and _build_request_payload().

Covers the currently active mini-proxy `json_object` fallback path, plus a
capability guard for the opt-in strict structured-output path so the HTTP 400
schema rejection documented in docs/api_schema_error_analysis.md cannot
silently return when `supports_structured_output` is switched on.
"""

import unittest

from modules.classify.src.orchestrator import (
    JSON_SCHEMA,
    REQUIRED_STABLE_FIELDS,
    _build_messages,
    _build_request_payload,
)
from modules.classify.tests.helpers import make_config

EXPERIMENTAL_FIELDS = ["content_timeliness", "primary_evidence_type"]


class TestBuildMessages(unittest.TestCase):
    def test_interpolates_title_and_sanitized_text(self) -> None:
        config = make_config(user_prompt_template="Title: {title} | Text: {sanitized_text}")
        messages = _build_messages(config, title="Hearing Today", sanitized_text="Committee met.")
        self.assertEqual(messages["user_prompt"], "Title: Hearing Today | Text: Committee met.")

    def test_system_instruction_passes_through(self) -> None:
        config = make_config(system_instruction="Custom system instruction.")
        messages = _build_messages(config, title="t", sanitized_text="x")
        self.assertEqual(messages["system_instruction"], "Custom system instruction.")


class TestFallbackPayload(unittest.TestCase):
    def test_json_object_fallback_without_strict_schema(self) -> None:
        config = make_config(supports_structured_output=False)
        payload = _build_request_payload(config, title="t", sanitized_text="x")
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_payload_derives_sampling_and_model_from_config(self) -> None:
        config = make_config(
            supports_structured_output=False,
            model_name="unit-test-model",
            temperature=0.33,
            top_p=0.77,
            max_output_tokens=321,
        )
        payload = _build_request_payload(config, title="t", sanitized_text="x")
        self.assertEqual(payload["model"], "unit-test-model")
        self.assertEqual(payload["temperature"], 0.33)
        self.assertEqual(payload["top_p"], 0.77)
        self.assertEqual(payload["max_tokens"], 321)

    def test_payload_message_roles_and_content(self) -> None:
        config = make_config(supports_structured_output=False)
        payload = _build_request_payload(config, title="My Title", sanitized_text="My Text")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertIn("My Title", payload["messages"][1]["content"])
        self.assertIn("My Text", payload["messages"][1]["content"])


class TestStructuredOutputGuard(unittest.TestCase):
    """Switch guard for the strict structured-output path: it is inactive
    under the current mini-proxy config, and these tests pin the exact schema
    shape required the moment a structured-capable provider is enabled."""

    def test_strict_payload_shape(self) -> None:
        config = make_config(supports_structured_output=True)
        payload = _build_request_payload(config, title="t", sanitized_text="x")
        response_format = payload["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        json_schema = response_format["json_schema"]
        self.assertEqual(json_schema["name"], "classification_result")
        self.assertIs(json_schema["strict"], True)
        self.assertIs(json_schema["schema"], JSON_SCHEMA)

    def test_schema_disallows_additional_properties(self) -> None:
        self.assertIs(JSON_SCHEMA["additionalProperties"], False)

    def test_schema_required_covers_stable_and_experimental_fields(self) -> None:
        self.assertEqual(
            set(JSON_SCHEMA["required"]),
            set(REQUIRED_STABLE_FIELDS) | set(EXPERIMENTAL_FIELDS),
        )

    def test_experimental_fields_are_required_but_nullable(self) -> None:
        for field in EXPERIMENTAL_FIELDS:
            with self.subTest(field=field):
                prop = JSON_SCHEMA["properties"][field]
                self.assertIn("string", prop["type"])
                self.assertIn("null", prop["type"])
                self.assertIn(None, prop["enum"])


if __name__ == "__main__":
    unittest.main()
