"""Execution-policy regression tests (TRANSLATE_TEST_MAINTAINABILITY_PLAN).

Phase 1 scope:
- General (non-429) HTTP 4xx fails after exactly one request without
  consuming the retry budget; 429 remains retryable (EXECUTION_POLICY.md
  section 4).
- --dry-run holds the multi-process runner lock while issuing real LLM API
  requests; --preview-prompts never acquires it (EXECUTION_POLICY.md
  section 3).

Phase 2 scope:
- _build_request_payload() interpolation, request defaults, and
  response_format fallback / strict structured-output shapes.
- _parse_response_content() envelope and content failure modes.
- Retry eligibility matrix (429, 5xx, timeout, network, parsing, validation)
  with deterministic call counts and policy-derived backoff.
- Rate-limit stagger derivation from rate_limit_per_minute.
- asyncio.Semaphore in-flight cap from max_concurrent_requests.
- Cross-process ProcessLock rejection and re-acquisition.

All tests are deterministic: fake HTTP client, patched asyncio.sleep and
jitter source. No real network, no real waiting, no workspace canonical DB,
no .env reads.
"""

import asyncio
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import patch

import httpx

from modules.translate.src.database import get_connection
from modules.translate.src.orchestrator import (
    NonRetryableLLMError,
    ProcessLock,
    _build_request_payload,
    _parse_response_content,
    fetch_llm_translation,
    orchestrate_run,
)
from modules.translate.tests import support


def _source_item_dict() -> Dict[str, Any]:
    """Minimal approved-content item as consumed by fetch_llm_translation."""
    return {
        "display_title": "Source Title",
        "summary_short": "Source summary text.",
        "bullet_1": "Claim content.",
        "bullet_2": "Evidence content.",
        "bullet_3": "Impact content.",
    }


def _zh_only_config(**overrides):
    kwargs: Dict[str, Any] = {
        "target_languages": {
            "zh": support.make_target_language(
                label="Traditional Chinese", max_title_length=120
            )
        },
        "supports_structured_output": False,
        # 5.0 is the locked production policy (commit bc165eb); mocks must not
        # resurrect the superseded 1.2 value.
        "content_ratio_limit": 5.0,
        "retry_attempts": 3,
        "backoff_factor": 0.1,
    }
    kwargs.update(overrides)
    return support.build_mock_config(**kwargs)


class _NoWaitMixin:
    """Patches asyncio.sleep and the jitter source for one test body."""

    def run_no_wait(self, coroutine_factory):
        """Runs the coroutine with asyncio.sleep and random.uniform patched.

        Returns (result, raised_exception_or_None, recorded_sleep_durations)
        so callers can assert on failures and backoff timing deterministically.
        """
        sleeps: List[float] = []

        async def fake_sleep(duration: float) -> None:
            sleeps.append(duration)

        result: Any = None
        error: Any = None
        with patch(
            "modules.translate.src.orchestrator.asyncio.sleep", new=fake_sleep
        ), patch(
            "modules.translate.src.orchestrator.random.uniform", return_value=0.0
        ):
            try:
                result = asyncio.run(coroutine_factory())
            except Exception as exc:  # noqa: BLE001 - returned for assertions
                error = exc
        return result, error, sleeps


class TestGeneralClientErrorsNoRetry(_NoWaitMixin, unittest.TestCase):
    """Regression: non-429 4xx used to fall into the broad httpx.HTTPError
    retry handler and burn the whole retry budget on a permanent error."""

    def test_general_4xx_fails_after_exactly_one_request(self) -> None:
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                client = support.FakeLLMClient()
                client.queue_response(status)

                def run():
                    return fetch_llm_translation(
                        client=client,
                        config=_zh_only_config(),
                        item=_source_item_dict(),
                        target_language_code="zh",
                        api_key="test-key",
                    )

                result, error, sleeps = self.run_no_wait(run)

                self.assertIsNone(result)
                self.assertIsInstance(error, NonRetryableLLMError)
                self.assertIn(str(status), str(error))
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(sleeps, [])

    def test_429_remains_retryable_and_uses_full_retry_budget(self) -> None:
        # Contrast case pinning that the fix only excluded non-429 4xx.
        retry_attempts = 3
        backoff_factor = 0.1
        client = support.FakeLLMClient()
        for _ in range(retry_attempts):
            client.queue_response(429)

        def run():
            return fetch_llm_translation(
                client=client,
                config=_zh_only_config(
                    retry_attempts=retry_attempts, backoff_factor=backoff_factor
                ),
                item=_source_item_dict(),
                target_language_code="zh",
                api_key="test-key",
            )

        result, error, sleeps = self.run_no_wait(run)

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertNotIsInstance(error, NonRetryableLLMError)
        self.assertIn(f"after {retry_attempts} attempts", str(error))
        self.assertEqual(len(client.requests), retry_attempts)
        # backoff_factor ** attempt with jitter pinned to 0.0
        self.assertEqual(sleeps, [backoff_factor ** 1, backoff_factor ** 2])


class TestDryRunProcessLock(unittest.TestCase):
    """Regression: dry-run used to skip the process lock while still calling
    the LLM API, allowing a dry-run and a normal run to duplicate API
    execution on the same queue."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = pathlib.Path(self.temp_dir.name)
        self.db_path = support.build_temp_workspace(self.workspace)
        self.lock_path = self.workspace / "data" / "translate_runner.lock"
        conn = get_connection(self.db_path)
        try:
            support.seed_approved_record(
                conn,
                parent_content_id=1,
                source_item_id=100,
                display_title="English Title",
                summary_short="English summary content.",
                bullet_1="Claim content.",
                bullet_2="Evidence content.",
                bullet_3="Impact content.",
                content_fingerprint="fp_test",
                content_language_code="en",
            )
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dry_run_refuses_to_run_when_lock_is_held(self) -> None:
        # Another process holds the runner lock: dry-run must abort instead of
        # duplicating API execution.
        with patch(
            "modules.translate.src.orchestrator.ProcessLock.acquire",
            side_effect=RuntimeError(
                "Could not acquire lock. Another process is running."
            ),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(
                    orchestrate_run(
                        config=_zh_only_config(),
                        db_path=self.db_path,
                        dry_run=True,
                    )
                )

    def test_preview_prompts_never_acquires_lock(self) -> None:
        with patch(
            "modules.translate.src.orchestrator.ProcessLock.acquire"
        ) as mock_acquire:
            summary = asyncio.run(
                orchestrate_run(
                    config=_zh_only_config(),
                    db_path=self.db_path,
                    preview_prompts=True,
                )
            )
        mock_acquire.assert_not_called()
        self.assertEqual(summary["status"], "preview")

    def test_dry_run_holds_lock_while_api_requests_are_in_flight(self) -> None:
        observed: Dict[str, Any] = {}
        fake_response = support.make_http_response(
            200,
            support.make_chat_completion_body(
                support.make_five_field_response(
                    title="標題",
                    summary="這是翻譯摘要內容。",
                    bullet_1="要點一內容。",
                    bullet_2="要點二內容。",
                    bullet_3="要點三內容。",
                )
            ),
        )

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            observed["lock_existed_during_api_call"] = self_lock_path.exists()
            observed["request_url"] = url
            return fake_response

        self_lock_path = self.lock_path
        with patch.dict(
            "os.environ", {"TEST_TRANSLATE_API_KEY": "test-key"}
        ), patch("httpx.AsyncClient.post", new=fake_post):
            summary = asyncio.run(
                orchestrate_run(
                    config=_zh_only_config(),
                    db_path=self.db_path,
                    dry_run=True,
                )
            )

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertTrue(
            observed.get("lock_existed_during_api_call"),
            "dry-run must hold the process lock while LLM API requests run",
        )
        # Lock is released (and the lock file removed) after the run.
        self.assertFalse(self.lock_path.exists())


# ---------------------------------------------------------------------------
# Phase 2: request payload construction
# ---------------------------------------------------------------------------

def _payload_item() -> Dict[str, Any]:
    return {
        "parent_content_id": 1,
        "display_title": "Source Title",
        "summary_short": "Source summary text.",
        "bullet_1": "Claim content.",
        "bullet_2": None,
        "bullet_3": "Impact content.",
    }


class TestBuildRequestPayload(unittest.TestCase):
    """_build_request_payload(): prompt interpolation of all five content
    fields, target language label, request defaults, and response_format
    fallback / strict structured-output shapes."""

    def test_interpolates_five_fields_and_target_language_label(self) -> None:
        payload = _build_request_payload(_zh_only_config(), _payload_item(), "zh")
        messages = payload["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "System Instruction")
        user = messages[1]["content"]
        self.assertIn("Traditional Chinese (zh)", user)
        self.assertIn("Source Title", user)
        self.assertIn("Source summary text.", user)
        self.assertIn("Claim content.", user)
        self.assertIn("Impact content.", user)
        # NULL source bullet rendered as the JSON literal null.
        self.assertIn("B2: null", user)

    def test_model_and_request_defaults_come_from_config(self) -> None:
        payload = _build_request_payload(_zh_only_config(), _payload_item(), "zh")
        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["temperature"], 0.3)
        self.assertEqual(payload["top_p"], 0.95)
        self.assertEqual(payload["max_tokens"], 4096)

    def test_top_p_omitted_when_unset(self) -> None:
        config = _zh_only_config(top_p=None)
        payload = _build_request_payload(config, _payload_item(), "zh")
        self.assertNotIn("top_p", payload)

    def test_json_object_fallback_when_structured_output_unsupported(self) -> None:
        config = _zh_only_config(supports_structured_output=False)
        payload = _build_request_payload(config, _payload_item(), "zh")
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_strict_structured_output_schema(self) -> None:
        config = _zh_only_config(supports_structured_output=True)
        payload = _build_request_payload(config, _payload_item(), "zh")
        fmt = payload["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        schema = fmt["json_schema"]
        self.assertTrue(schema["strict"])
        body = schema["schema"]
        self.assertEqual(body["properties"]["translated_title"]["type"], "string")
        for key in ("translated_bullet_1", "translated_bullet_2", "translated_bullet_3"):
            self.assertEqual(body["properties"][key]["type"], ["string", "null"])
        self.assertEqual(
            set(body["required"]),
            {
                "translated_title",
                "translated_summary",
                "translated_bullet_1",
                "translated_bullet_2",
                "translated_bullet_3",
            },
        )
        self.assertFalse(body["additionalProperties"])

    def test_real_active_template_interpolates_all_fields(self) -> None:
        # Guards against placeholder drift in the shipped prompt template.
        config = support.load_active_config()
        payload = _build_request_payload(config, _payload_item(), "zh")
        user = payload["messages"][1]["content"]
        for value in ("Source Title", "Source summary text.", "Claim content.", "Impact content."):
            self.assertIn(value, user)
        # Active provider capability: mini-proxy has no structured output.
        self.assertEqual(payload["response_format"], {"type": "json_object"})


# ---------------------------------------------------------------------------
# Phase 2: response envelope parsing
# ---------------------------------------------------------------------------

class TestParseResponseContent(unittest.TestCase):
    """_parse_response_content(): empty choices, missing/empty/non-string
    content, refusal, invalid JSON, and valid five-field JSON."""

    def test_valid_five_field_json_parses(self) -> None:
        body = support.make_chat_completion_body(
            support.make_five_field_response(
                title="T", summary="S", bullet_1=None, bullet_2=None, bullet_3=None
            )
        )
        data = _parse_response_content(support.make_http_response(200, body))
        self.assertEqual(data["translated_title"], "T")
        self.assertEqual(data["translated_summary"], "S")
        self.assertIsNone(data["translated_bullet_1"])

    def test_missing_or_empty_choices_rejected(self) -> None:
        for body in ({}, {"choices": []}):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    _parse_response_content(support.make_http_response(200, body))

    def test_missing_message_content_rejected(self) -> None:
        body = {"choices": [{"message": {}}]}
        with self.assertRaises(ValueError):
            _parse_response_content(support.make_http_response(200, body))

    def test_empty_or_whitespace_content_rejected(self) -> None:
        for content in ("", "   "):
            with self.subTest(content=content):
                body = {"choices": [{"message": {"content": content}}]}
                with self.assertRaises(ValueError):
                    _parse_response_content(support.make_http_response(200, body))

    def test_non_string_content_rejected(self) -> None:
        for content in (123, ["x"], None, {"k": "v"}):
            with self.subTest(content=content):
                body = {"choices": [{"message": {"content": content}}]}
                with self.assertRaises(ValueError):
                    _parse_response_content(support.make_http_response(200, body))

    def test_refusal_rejected(self) -> None:
        body = {"choices": [{"message": {"refusal": "cannot translate", "content": None}}]}
        with self.assertRaises(ValueError) as ctx:
            _parse_response_content(support.make_http_response(200, body))
        self.assertIn("refused", str(ctx.exception))

    def test_invalid_json_raises_decode_error(self) -> None:
        response = support.make_http_response(200, raw_content=b"this is not json")
        with self.assertRaises(json.JSONDecodeError):
            _parse_response_content(response)


# ---------------------------------------------------------------------------
# Phase 2: retry eligibility matrix
# ---------------------------------------------------------------------------

class TestRetryEligibilityMatrix(_NoWaitMixin, unittest.TestCase):
    """fetch_llm_translation(): retry eligibility, request counts and
    policy-derived backoff per failure class (EXECUTION_POLICY.md section 4).
    General non-429 4xx is covered by TestGeneralClientErrorsNoRetry."""

    RETRY_ATTEMPTS = 3
    BACKOFF = 0.1

    def _config(self):
        return support.build_mock_config(
            target_languages={
                "en": support.make_target_language(label="English", max_title_length=500)
            },
            supports_structured_output=False,
            content_ratio_limit=5.0,
            retry_attempts=self.RETRY_ATTEMPTS,
            backoff_factor=self.BACKOFF,
        )

    @staticmethod
    def _item() -> Dict[str, Any]:
        return {
            "display_title": "T",
            "summary_short": "S" * 50,
            "bullet_1": None,
            "bullet_2": None,
            "bullet_3": None,
        }

    def _run(self, client: support.FakeLLMClient):
        def coro():
            return fetch_llm_translation(
                client=client,
                config=self._config(),
                item=self._item(),
                target_language_code="en",
                api_key="test-key",
            )

        return self.run_no_wait(coro)

    def _assert_budget_exhausted(self, error, client, sleeps) -> None:
        self.assertIsInstance(error, RuntimeError)
        self.assertNotIsInstance(error, NonRetryableLLMError)
        self.assertIn(f"after {self.RETRY_ATTEMPTS} attempts", str(error))
        self.assertEqual(len(client.requests), self.RETRY_ATTEMPTS)
        # backoff_factor ** attempt with jitter pinned to 0.0
        self.assertEqual(sleeps, [self.BACKOFF, self.BACKOFF ** 2])

    def test_retryable_http_statuses_exhaust_retry_budget(self) -> None:
        for status in (429, 500, 503):
            with self.subTest(status=status):
                client = support.FakeLLMClient()
                for _ in range(self.RETRY_ATTEMPTS):
                    client.queue_response(status)
                result, error, sleeps = self._run(client)
                self.assertIsNone(result)
                self._assert_budget_exhausted(error, client, sleeps)

    def test_timeout_and_network_errors_are_retryable(self) -> None:
        for exc_type in (httpx.TimeoutException, httpx.ConnectError):
            with self.subTest(exc_type=exc_type.__name__):
                client = support.FakeLLMClient()
                for _ in range(self.RETRY_ATTEMPTS):
                    client.queue_exception(exc_type("simulated failure"))
                result, error, sleeps = self._run(client)
                self.assertIsNone(result)
                self._assert_budget_exhausted(error, client, sleeps)

    def test_response_parsing_failure_is_retryable(self) -> None:
        client = support.FakeLLMClient()
        for _ in range(self.RETRY_ATTEMPTS):
            client.queue_response(200, raw_content=b"this is not json")
        result, error, sleeps = self._run(client)
        self.assertIsNone(result)
        self._assert_budget_exhausted(error, client, sleeps)

    def test_validation_failure_is_retryable(self) -> None:
        # Missing required keys -> runner-side validation ValueError.
        client = support.FakeLLMClient()
        body = support.make_chat_completion_body({"translated_title": "T"})
        for _ in range(self.RETRY_ATTEMPTS):
            client.queue_response(200, body)
        result, error, sleeps = self._run(client)
        self.assertIsNone(result)
        self._assert_budget_exhausted(error, client, sleeps)

    def test_transient_failure_then_success_retries_once(self) -> None:
        client = support.FakeLLMClient()
        client.queue_exception(httpx.ConnectError("simulated failure"))
        client.queue_response(
            200,
            support.make_chat_completion_body(
                support.make_five_field_response(
                    title="Title",
                    summary="Translated summary.",
                    bullet_1=None,
                    bullet_2=None,
                    bullet_3=None,
                )
            ),
        )
        result, error, sleeps = self._run(client)
        self.assertIsNone(error)
        self.assertEqual(result["translated_summary"], "Translated summary.")
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(sleeps, [self.BACKOFF])


# ---------------------------------------------------------------------------
# Phase 2: rate-limit stagger and semaphore (orchestrator level)
# ---------------------------------------------------------------------------

def _valid_zh_body() -> Dict[str, Any]:
    """zh response passing all runner-side validation rules."""
    return support.make_chat_completion_body(
        support.make_five_field_response(
            title="標題",
            summary="這是一段中文翻譯摘要內容。",
            bullet_1="第一要點內容。",
            bullet_2="第二要點內容。",
            bullet_3="第三要點內容。",
        )
    )


class _OrchestratorDBBase(unittest.TestCase):
    """Temp workspace DB seeded with English approved records whose zh tasks
    all require (mocked) LLM calls."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = pathlib.Path(self.temp_dir.name)
        self.db_path = support.build_temp_workspace(self.workspace)

    def seed_english_records(self, count: int, *, start_id: int = 1) -> None:
        conn = get_connection(self.db_path)
        try:
            for offset in range(count):
                pid = start_id + offset
                support.seed_approved_record(
                    conn,
                    parent_content_id=pid,
                    source_item_id=1000 + pid,
                    display_title=f"English Title {pid}",
                    summary_short=f"English summary content {pid}.",
                    bullet_1="Claim content.",
                    bullet_2="Evidence content.",
                    bullet_3="Impact content.",
                    content_fingerprint=f"fp_{pid}",
                    content_language_code="en",
                )
        finally:
            conn.close()


class TestRateLimitStagger(_OrchestratorDBBase):
    """Worker stagger delay derives from rate_limit_per_minute as 60/rpm.
    Expected values are computed from the test config; no historical or
    active-config number (20/60/1200) is hardcoded as a universal contract."""

    def test_stagger_delays_derive_from_config_rpm(self) -> None:
        rpm = 120
        expected_delay = 60.0 / rpm
        task_count = 3
        self.seed_english_records(task_count)
        config = _zh_only_config(rate_limit_per_minute=rpm)

        sleeps: List[float] = []

        async def fake_sleep(duration: float) -> None:
            sleeps.append(duration)

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            return support.make_http_response(200, _valid_zh_body())

        with patch.dict(
            "os.environ", {"TEST_TRANSLATE_API_KEY": "test-key"}
        ), patch(
            "modules.translate.src.orchestrator.asyncio.sleep", new=fake_sleep
        ), patch("httpx.AsyncClient.post", new=fake_post):
            summary = asyncio.run(orchestrate_run(config=config, db_path=self.db_path))

        self.assertEqual(summary["processed_successfully"], task_count)
        # Worker idx sleeps idx * (60/rpm) before its request; idx 0 does not.
        self.assertEqual(
            sorted(sleeps),
            sorted(expected_delay * idx for idx in range(1, task_count)),
        )


class TestSemaphoreConcurrencyLimit(_OrchestratorDBBase):
    """asyncio.Semaphore must keep in-flight HTTP requests at or below the
    configured max_concurrent_requests, regardless of completion order."""

    def test_in_flight_requests_never_exceed_config_limit(self) -> None:
        limit = 2
        task_count = 6
        self.seed_english_records(task_count)
        config = _zh_only_config(
            max_concurrent_requests=limit,
            # Very high rpm keeps stagger delays negligible for this test.
            rate_limit_per_minute=600000,
        )

        state = {"in_flight": 0, "max_in_flight": 0}

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
            # Short real yield forces actual overlap of concurrent workers.
            await asyncio.sleep(0.02)
            state["in_flight"] -= 1
            return support.make_http_response(200, _valid_zh_body())

        with patch.dict(
            "os.environ", {"TEST_TRANSLATE_API_KEY": "test-key"}
        ), patch("httpx.AsyncClient.post", new=fake_post):
            summary = asyncio.run(orchestrate_run(config=config, db_path=self.db_path))

        self.assertEqual(summary["processed_successfully"], task_count)
        self.assertLessEqual(
            state["max_in_flight"],
            limit,
            "semaphore allowed more in-flight requests than configured",
        )
        self.assertGreaterEqual(
            state["max_in_flight"],
            2,
            "workers never overlapped; the limit was not actually exercised",
        )


# ---------------------------------------------------------------------------
# Phase 2: process lock across processes
# ---------------------------------------------------------------------------

class TestProcessLockCrossProcess(unittest.TestCase):
    """ProcessLock: second process is rejected while the lock is held and can
    acquire it again after release. Subprocess calls are timeout-protected and
    the lock file lives in a temporary workspace."""

    REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

    def test_acquire_release_reacquire_same_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = pathlib.Path(tmp) / "data" / "runner.lock"
            lock = ProcessLock(lock_path)
            lock.acquire()
            self.assertTrue(lock_path.exists())
            lock.release()
            self.assertFalse(lock_path.exists())

            lock2 = ProcessLock(lock_path)
            lock2.acquire()
            lock2.release()
            self.assertFalse(lock_path.exists())

    def test_second_process_rejected_until_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = pathlib.Path(tmp) / "data" / "runner.lock"
            holder = ProcessLock(lock_path)
            holder.acquire()
            try:
                rejected = self._acquire_in_subprocess(lock_path)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("Could not acquire lock", rejected.stderr)
            finally:
                holder.release()

            acquired = self._acquire_in_subprocess(lock_path)
            self.assertEqual(acquired.returncode, 0, acquired.stderr)
            self.assertIn("ACQUIRED", acquired.stdout)

    def _acquire_in_subprocess(self, lock_path: pathlib.Path) -> subprocess.CompletedProcess:
        code = (
            "import pathlib;"
            "from modules.translate.src.orchestrator import ProcessLock;"
            f"lock = ProcessLock(pathlib.Path({str(lock_path)!r}));"
            "lock.acquire();"
            "print('ACQUIRED');"
            "lock.release();"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(self.REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
