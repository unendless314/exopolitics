"""Ratio-limit config regression tests (TRANSLATE_TEST_MAINTAINABILITY_PLAN
Phase 1, ratio batch).

The locked production policy is content_ratio_limit = 5.0 (deliberately
relaxed from 1.2 in commit bc165eb, 2026-06-23). These tests pin the active
config, the Pydantic default, and prove a limit loaded from YAML actually
flows into fetch_llm_translation() validation — instead of trusting
handwritten mock values as runtime behavior.
"""

import asyncio
import pathlib
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import patch

from modules.translate.src.config import ValidationConfig, validate_and_load_config
from modules.translate.src.orchestrator import fetch_llm_translation
from modules.translate.tests import support


def _source_item_dict() -> Dict[str, Any]:
    return {
        "display_title": "Source Title",
        "summary_short": "S" * 100,
        "bullet_1": None,
        "bullet_2": None,
        "bullet_3": None,
    }


def _ratio_3_response_body() -> Dict[str, Any]:
    """Response whose aggregate ratio vs the 100-char source summary is 3.0."""
    return support.make_chat_completion_body(
        support.make_five_field_response(
            title="Title",
            summary="T" * 300,
            bullet_1=None,
            bullet_2=None,
            bullet_3=None,
        )
    )


def _en_target_languages() -> Dict[str, Dict[str, Any]]:
    return {"en": {"label": "English", "max_title_length": 500}}


class TestLockedRatioPolicy(unittest.TestCase):
    def test_active_config_ratio_limit_is_the_locked_5_0(self) -> None:
        config = support.load_active_config()
        self.assertEqual(config.validation.content_ratio_limit, 5.0)

    def test_pydantic_default_ratio_limit_is_the_locked_5_0(self) -> None:
        # An omitted validation.content_ratio_limit key must not silently
        # resurrect the superseded 1.2 value.
        self.assertEqual(ValidationConfig().content_ratio_limit, 5.0)


class TestYamlRatioLimitFlowsIntoFetch(unittest.TestCase):
    """Integration: the limit parsed from a temporary YAML config dir is the
    limit fetch_llm_translation() actually enforces."""

    def _run_fetch(self, config_dir: pathlib.Path, scripted_responses: int):
        config = validate_and_load_config(config_dir)
        client = support.FakeLLMClient()
        body = _ratio_3_response_body()
        for _ in range(scripted_responses):
            client.queue_response(200, body)

        sleeps: List[float] = []

        async def fake_sleep(duration: float) -> None:
            sleeps.append(duration)

        async def invoke():
            return await fetch_llm_translation(
                client=client,
                config=config,
                item=_source_item_dict(),
                target_language_code="en",
                api_key="test-key",
            )

        result: Any = None
        error: Any = None
        with patch(
            "modules.translate.src.orchestrator.asyncio.sleep", new=fake_sleep
        ), patch(
            "modules.translate.src.orchestrator.random.uniform", return_value=0.0
        ):
            try:
                result = asyncio.run(invoke())
            except Exception as exc:  # noqa: BLE001 - returned for assertions
                error = exc
        return result, error, client, sleeps

    def test_limit_below_ratio_fails_after_full_retry_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = support.write_config_dir(
                pathlib.Path(tmp) / "config",
                content_ratio_limit=1.5,
                supports_structured_output=False,
                target_languages=_en_target_languages(),
                retry_attempts=3,
            )
            result, error, client, _sleeps = self._run_fetch(config_dir, 3)

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        # The enforced limit in the failure message is the YAML-loaded 1.5,
        # proving the config value flowed into validation.
        self.assertIn("exceeds limit of 1.5", str(error))
        # Validation failures are retryable per EXECUTION_POLICY.md section 5.
        self.assertEqual(len(client.requests), 3)

    def test_limit_above_ratio_passes_in_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = support.write_config_dir(
                pathlib.Path(tmp) / "config",
                content_ratio_limit=5.0,
                supports_structured_output=False,
                target_languages=_en_target_languages(),
            )
            result, error, client, _sleeps = self._run_fetch(config_dir, 1)

        self.assertIsNone(error)
        self.assertEqual(result["translated_summary"], "T" * 300)
        self.assertEqual(len(client.requests), 1)


class TestTopPOptionalConfig(unittest.TestCase):
    """top_p is an optional request parameter (GPT_5_6_LUNA_TOP_P_PATCH_PLAN):
    YAML `null` loads as None, and the shipped active config pins the
    incident baseline (temperature 1.0, top_p unset)."""

    def test_null_top_p_in_yaml_loads_as_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = support.write_config_dir(
                pathlib.Path(tmp) / "config",
                content_ratio_limit=5.0,
                supports_structured_output=False,
                top_p=None,
            )
            config = validate_and_load_config(config_dir)

        self.assertIsNone(config.request_defaults.top_p)

    def test_active_config_top_p_is_unset_and_temperature_is_1_0(self) -> None:
        config = support.load_active_config()
        self.assertEqual(config.request_defaults.temperature, 1.0)
        self.assertIsNone(config.request_defaults.top_p)


class TestBatchSizePositiveIntegerConfig(unittest.TestCase):
    """Code-review P1 (2026-08-01): execution_policy.batch_size must be a
    positive integer. Non-positive YAML values are rejected at config
    validation, so a bad config can never reach orchestrate_run's batch
    selection (which re-validates the effective value as well)."""

    def test_non_positive_batch_size_in_yaml_fails_validation(self) -> None:
        for bad_value in (0, -5):
            with self.subTest(batch_size=bad_value):
                with tempfile.TemporaryDirectory() as tmp:
                    config_dir = support.write_config_dir(
                        pathlib.Path(tmp) / "config",
                        content_ratio_limit=5.0,
                        supports_structured_output=False,
                        batch_size=bad_value,
                    )
                    # pydantic.ValidationError subclasses ValueError.
                    with self.assertRaises(ValueError):
                        validate_and_load_config(config_dir)


if __name__ == "__main__":
    unittest.main()
