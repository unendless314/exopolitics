"""State machine, queue selection, forced rerun, dry-run/preview and batch
semantics regression tests (TRANSLATE_TEST_MAINTAINABILITY_PLAN).

Plan section 3.5 and Phase 3 work items 1-4, all exercised through the public
orchestrator path orchestrate_run():

- Bulk queue eligibility matrix: no row / pending / stale / retryable failed /
  locked failed / completed / bypassed completed (item 1).
- retry_count increments by exactly 1 per failing run until the logical lock
  (retry_count >= retry_attempts) excludes the task from the bulk queue
  (items 1-2).
- Operator force via single-task mode reruns a locked failed row and resets
  retry_count on success (item 2).
- Fresh completed forced rerun is non-persisted: success atomically overwrites
  the five content fields and refreshes translated_at; API failure leaves the
  completed row completely unchanged (items 2-3, section 7 conclusion 8).
- A stale row is not protected by the fresh-completed model even under
  --force: it follows the normal stale retry path (item 3).
- dry_run=True issues real (mocked) LLM API requests but persists nothing and
  skips stale detection; preview_prompts=True never calls the API (item 3).
- batch_size counts SOURCE ITEMS selected by approved_at ASC,
  parent_content_id ASC; a batch boundary never splits one article's eligible
  language set; completed languages are not redone; the run summary reports
  both source_items and total_queried (item 4, section 7 conclusion 6).

All tests are deterministic: mocked httpx.AsyncClient.post, patched
asyncio.sleep and random.uniform on every failure/retry path, temporary
workspace DB, no workspace canonical DB reads, no .env reads, no real HTTP.
"""

import asyncio
import pathlib
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from modules.translate.src.database import get_connection
from modules.translate.src.orchestrator import orchestrate_run
from modules.translate.tests import support

ZH_LABEL = "Traditional Chinese"
JA_LABEL = "Japanese"


def _zh_only_config(**overrides: Any):
    """zh-only mock config; 5.0 is the locked production ratio policy."""
    kwargs: Dict[str, Any] = {
        "target_languages": {
            "zh": support.make_target_language(label=ZH_LABEL, max_title_length=120)
        },
        "supports_structured_output": False,
        "content_ratio_limit": 5.0,
        "retry_attempts": 3,
        "backoff_factor": 0.1,
    }
    kwargs.update(overrides)
    return support.build_mock_config(**kwargs)


def _zh_ja_config(**overrides: Any):
    """zh+ja mock config for multi-language batch semantics tests."""
    kwargs: Dict[str, Any] = {
        "target_languages": {
            "zh": support.make_target_language(label=ZH_LABEL, max_title_length=120),
            "ja": support.make_target_language(label=JA_LABEL, max_title_length=120),
        },
        "supports_structured_output": False,
        "content_ratio_limit": 5.0,
        "retry_attempts": 3,
        "backoff_factor": 0.1,
    }
    kwargs.update(overrides)
    return support.build_mock_config(**kwargs)


def _valid_zh_body(**field_overrides: Any) -> Dict[str, Any]:
    """zh response passing all runner-side validation rules (CJK present)."""
    fields: Dict[str, Any] = {
        "title": "標題",
        "summary": "這是一段中文翻譯摘要內容。",
        "bullet_1": "第一要點內容。",
        "bullet_2": "第二要點內容。",
        "bullet_3": "第三要點內容。",
    }
    fields.update(field_overrides)
    return support.make_chat_completion_body(support.make_five_field_response(**fields))


def _valid_ja_body() -> Dict[str, Any]:
    """ja response passing all runner-side validation rules (kana present)."""
    return support.make_chat_completion_body(
        support.make_five_field_response(
            title="タイトル",
            summary="これは翻訳された要約内容です。",
            bullet_1="第一の要点内容です。",
            bullet_2="第二の要点内容です。",
            bullet_3="第三の要点内容です。",
        )
    )


class _OrchestrateBase(unittest.TestCase):
    """Temporary workspace DB plus a deterministic orchestrate_run() driver."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = pathlib.Path(self.temp_dir.name)
        self.db_path = support.build_temp_workspace(self.workspace)

    # -- seeding -----------------------------------------------------------

    def seed_record(
        self,
        *,
        parent_content_id: int,
        content_fingerprint: Optional[str] = None,
        content_language_code: str = "en",
        approved_at: str = "2026-06-20T12:00:00Z",
    ) -> None:
        conn = get_connection(self.db_path)
        try:
            support.seed_approved_record(
                conn,
                parent_content_id=parent_content_id,
                source_item_id=100 + parent_content_id,
                display_title=f"English Title {parent_content_id}",
                summary_short=f"English summary content {parent_content_id}.",
                bullet_1="Claim content.",
                bullet_2="Evidence content.",
                bullet_3="Impact content.",
                content_fingerprint=content_fingerprint or f"fp_{parent_content_id}",
                content_language_code=content_language_code,
                approved_at=approved_at,
            )
        finally:
            conn.close()

    def seed_translation(self, **kwargs: Any) -> None:
        conn = get_connection(self.db_path)
        try:
            support.seed_translation_row(conn, **kwargs)
        finally:
            conn.close()

    # -- inspection --------------------------------------------------------

    def snapshot(
        self, parent_content_id: int, language_code: str = "zh"
    ) -> Optional[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        try:
            return support.snapshot_translation_row(
                conn, parent_content_id=parent_content_id, language_code=language_code
            )
        finally:
            conn.close()

    # -- fake HTTP ---------------------------------------------------------

    @staticmethod
    def success_post(
        requests: List[Dict[str, Any]], zh_body: Optional[Dict[str, Any]] = None
    ):
        """fake_post recording payloads; returns a valid per-language body."""

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            requests.append(json)
            prompt = json["messages"][1]["content"]
            if f"{JA_LABEL} (ja)" in prompt:
                return support.make_http_response(200, _valid_ja_body())
            return support.make_http_response(200, zh_body or _valid_zh_body())

        return fake_post

    @staticmethod
    def failing_post(requests: List[Dict[str, Any]], *, fail_marker: Optional[str] = None):
        """fake_post returning HTTP 500 for matching prompts (all when unmarked)."""

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            requests.append(json)
            prompt = json["messages"][1]["content"]
            if fail_marker is None or fail_marker in prompt:
                return support.make_http_response(500)
            if f"{JA_LABEL} (ja)" in prompt:
                return support.make_http_response(200, _valid_ja_body())
            return support.make_http_response(200, _valid_zh_body())

        return fake_post

    # -- runner ------------------------------------------------------------

    def run_orchestrator(
        self, *, config, fake_post, **run_kwargs
    ) -> Tuple[Dict[str, Any], List[float]]:
        """Runs orchestrate_run with env, no-wait and HTTP patches.

        asyncio.sleep (retry backoff and rate-limit stagger) and the jitter
        source are patched so failure/retry paths never actually wait.
        Returns (summary, recorded_sleep_durations).
        """
        sleeps: List[float] = []

        async def fake_sleep(duration: float) -> None:
            sleeps.append(duration)

        with patch.dict(
            "os.environ", {"TEST_TRANSLATE_API_KEY": "test-key"}
        ), patch(
            "modules.translate.src.orchestrator.asyncio.sleep", new=fake_sleep
        ), patch(
            "modules.translate.src.orchestrator.random.uniform", return_value=0.0
        ), patch(
            "httpx.AsyncClient.post", new=fake_post
        ):
            summary = asyncio.run(
                orchestrate_run(config=config, db_path=self.db_path, **run_kwargs)
            )
        return summary, sleeps


class TestBulkQueueEligibilityMatrix(_OrchestrateBase):
    """Phase 3 item 1 / section 3.5: bulk queue eligibility matrix through the
    public bulk path — no row, pending, stale, retryable failed, locked
    failed, completed and bypassed completed."""

    def test_bulk_run_processes_only_eligible_tasks(self) -> None:
        config = _zh_only_config()

        # pid -> queue state:
        # 1 no row (eligible), 2 pending (eligible), 3 stale (eligible),
        # 4 failed retry_count=1 < retry_attempts (eligible),
        # 5 failed retry_count=retry_attempts (locked, excluded),
        # 6 completed fresh (excluded), 7 bypassed completed (excluded).
        for pid in (1, 2, 3, 4, 5, 6):
            self.seed_record(parent_content_id=pid)
        # A bypassed row realistically belongs to a zh mother-draft whose zh
        # task was a self-translation.
        self.seed_record(parent_content_id=7, content_language_code="zh")

        self.seed_translation(
            parent_content_id=2, source_item_id=102, language_code="zh",
            display_title=None, summary_short=None,
            bullet_1=None, bullet_2=None, bullet_3=None,
            source_fingerprint="fp_2", status="pending", retry_count=0,
            translated_at=None,
        )
        self.seed_translation(
            parent_content_id=3, source_item_id=103, language_code="zh",
            display_title="舊標題三", summary_short="舊摘要三內容。",
            bullet_1="舊要點一內容。", bullet_2="舊要點二內容。", bullet_3="舊要點三內容。",
            source_fingerprint="fp_3", status="stale", retry_count=0,
        )
        self.seed_translation(
            parent_content_id=4, source_item_id=104, language_code="zh",
            display_title=None, summary_short=None,
            bullet_1=None, bullet_2=None, bullet_3=None,
            source_fingerprint="fp_4", status="failed", retry_count=1,
            translated_at=None,
        )
        self.seed_translation(
            parent_content_id=5, source_item_id=105, language_code="zh",
            display_title=None, summary_short=None,
            bullet_1=None, bullet_2=None, bullet_3=None,
            source_fingerprint="fp_5", status="failed",
            retry_count=config.execution_policy.retry_attempts,
            translated_at=None,
        )
        self.seed_translation(
            parent_content_id=6, source_item_id=106, language_code="zh",
            display_title="標題六", summary_short="摘要六內容。",
            bullet_1="要點一內容。", bullet_2="要點二內容。", bullet_3="要點三內容。",
            source_fingerprint="fp_6", status="completed", retry_count=0,
        )
        self.seed_translation(
            parent_content_id=7, source_item_id=107, language_code="zh",
            display_title="標題七", summary_short="摘要七內容。",
            bullet_1="要點一內容。", bullet_2="要點二內容。", bullet_3="要點三內容。",
            source_fingerprint="fp_7", status="completed", retry_count=0,
            model_name="bypass", prompt_version="bypass",
        )

        untouched_before = {pid: self.snapshot(pid) for pid in (5, 6, 7)}

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests)
        )

        self.assertEqual(
            summary,
            {
                "source_items": 4,
                "total_queried": 4,
                "processed_successfully": 4,
                "failures": 0,
                "status": "completed",
            },
        )

        # Exactly the four eligible (record, language) tasks hit the API.
        self.assertEqual(len(requests), 4)
        requested_pids = set()
        for payload in requests:
            prompt = payload["messages"][1]["content"]
            for pid in range(1, 8):
                if f"Title: English Title {pid}\n" in prompt:
                    requested_pids.add(pid)
        self.assertEqual(requested_pids, {1, 2, 3, 4})

        # Eligible rows completed with fresh content and reset retry counts.
        for pid in (1, 2, 3, 4):
            row = self.snapshot(pid)
            self.assertIsNotNone(row)
            self.assertEqual(row["translation_status"], "completed")
            self.assertEqual(row["retry_count"], 0)
            self.assertEqual(row["display_title"], "標題")
            self.assertEqual(row["summary_short"], "這是一段中文翻譯摘要內容。")
            self.assertEqual(row["source_fingerprint"], f"fp_{pid}")
            self.assertIsNotNone(row["translated_at"])

        # Locked / completed / bypassed rows are completely untouched.
        for pid, before in untouched_before.items():
            self.assertEqual(self.snapshot(pid), before)


class TestRetryCountProgression(_OrchestrateBase):
    """Phase 3 items 1-2 / section 3.5: retry_count increments by exactly 1
    per failing run; at retry_count == retry_attempts the task is logically
    locked and the bulk queue stops selecting it."""

    def test_retry_count_increments_to_logical_lock(self) -> None:
        config = _zh_only_config()
        attempts = config.execution_policy.retry_attempts
        self.seed_record(parent_content_id=1)

        for expected_retry_count in range(1, attempts + 1):
            requests: List[Dict[str, Any]] = []
            summary, _ = self.run_orchestrator(
                config=config, fake_post=self.failing_post(requests)
            )
            self.assertEqual(summary["failures"], 1)
            self.assertEqual(summary["processed_successfully"], 0)
            # Each run consumes the full per-task retry budget exactly once.
            self.assertEqual(len(requests), attempts)

            row = self.snapshot(1)
            self.assertEqual(row["translation_status"], "failed")
            self.assertEqual(row["retry_count"], expected_retry_count)
            # First-run failure contract: all five content fields stay NULL.
            for field in (
                "display_title", "summary_short", "bullet_1", "bullet_2", "bullet_3"
            ):
                self.assertIsNone(row[field])
            self.assertIsNone(row["translated_at"])

        locked_snapshot = self.snapshot(1)

        # Logically locked: the next bulk run selects nothing, issues no API
        # request, and leaves the row completely unchanged.
        requests = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.failing_post(requests)
        )
        self.assertEqual(requests, [])
        self.assertEqual(
            summary,
            {
                "source_items": 0,
                "total_queried": 0,
                "processed_successfully": 0,
                "failures": 0,
                "status": "completed",
            },
        )
        self.assertEqual(self.snapshot(1), locked_snapshot)


class TestPermanentClientErrorLocksImmediately(_OrchestrateBase):
    """Code-review P1 (2026-08-01) / EXECUTION_POLICY.md section 4: a
    permanent (non-429 4xx) client error cannot be fixed by an identical
    re-request. The task fails after exactly one API request and the row is
    written failed with retry_count at the retry limit, so the bulk queue
    never re-selects it and later runs burn no API quota on it."""

    @staticmethod
    def _permanent_error_post(requests: List[Dict[str, Any]], status_code: int):
        """fake_post always returning the given non-retryable status."""

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            requests.append(json)
            return support.make_http_response(status_code)

        return fake_post

    def test_permanent_4xx_locks_after_single_request(self) -> None:
        config = _zh_only_config()
        attempts = config.execution_policy.retry_attempts
        self.seed_record(parent_content_id=1)

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self._permanent_error_post(requests, 400)
        )
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["processed_successfully"], 0)
        # Permanent client error: exactly one request, no retry budget burned.
        self.assertEqual(len(requests), 1)

        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "failed")
        # Locked immediately at the retry limit, not incremented by one.
        self.assertEqual(row["retry_count"], attempts)
        for field in (
            "display_title", "summary_short", "bullet_1", "bullet_2", "bullet_3"
        ):
            self.assertIsNone(row[field])
        self.assertIsNone(row["translated_at"])
        locked_snapshot = self.snapshot(1)

        # The bulk queue never selects it again: a second run issues no API
        # request and leaves the row completely unchanged.
        requests = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self._permanent_error_post(requests, 400)
        )
        self.assertEqual(requests, [])
        self.assertEqual(
            summary,
            {
                "source_items": 0,
                "total_queried": 0,
                "processed_successfully": 0,
                "failures": 0,
                "status": "completed",
            },
        )
        self.assertEqual(self.snapshot(1), locked_snapshot)

    def test_operator_force_still_reruns_permanently_locked_row(self) -> None:
        config = _zh_only_config()
        self.seed_record(parent_content_id=1)

        requests: List[Dict[str, Any]] = []
        self.run_orchestrator(
            config=config, fake_post=self._permanent_error_post(requests, 400)
        )
        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "failed")
        self.assertEqual(row["retry_count"], config.execution_policy.retry_attempts)

        # Operator escape hatch: single-task force reruns the locked item and
        # resets retry_count on success.
        requests = []
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.success_post(requests),
            parent_content_id=1,
            force=True,
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(summary["processed_successfully"], 1)
        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "completed")
        self.assertEqual(row["retry_count"], 0)

    def test_upstream_content_change_releases_permanent_lock_via_stale(self) -> None:
        # Code-review P2 (2026-08-01) / STATE_TRANSITIONS.md section 1.1: the
        # permanent lock only pins the identical request. When the upstream
        # content fingerprint changes, stale detection flips the locked failed
        # row to stale and the next bulk run retries it automatically (the
        # retried request carries new content), with no --force needed.
        config = _zh_only_config()
        attempts = config.execution_policy.retry_attempts
        self.seed_record(parent_content_id=1)

        requests: List[Dict[str, Any]] = []
        self.run_orchestrator(
            config=config, fake_post=self._permanent_error_post(requests, 400)
        )
        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "failed")
        self.assertEqual(row["retry_count"], attempts)

        # Upstream edit: the approved content fingerprint changes.
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE approved_content_record SET content_fingerprint = 'fp_1_edited' "
                "WHERE parent_content_id = 1"
            )
            conn.commit()
        finally:
            conn.close()

        # The next plain bulk run re-selects the row (now stale) and completes
        # it, resetting retry_count.
        requests = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests)
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(summary["processed_successfully"], 1)
        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "completed")
        self.assertEqual(row["retry_count"], 0)


class TestOperatorForceOnLockedFailure(_OrchestrateBase):
    """Phase 3 item 2: operator force on a logically locked failed row
    (single-task mode) reruns it and resets retry_count on success."""

    def test_force_reruns_locked_failed_row_and_resets_retry_count(self) -> None:
        config = _zh_only_config()
        self.seed_record(parent_content_id=1)
        self.seed_translation(
            parent_content_id=1, source_item_id=101, language_code="zh",
            display_title=None, summary_short=None,
            bullet_1=None, bullet_2=None, bullet_3=None,
            source_fingerprint="fp_1", status="failed",
            retry_count=config.execution_policy.retry_attempts,
            translated_at=None,
        )
        before = self.snapshot(1)

        # Without force the locked row is not eligible even in single-task mode.
        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests), parent_content_id=1
        )
        self.assertEqual(requests, [])
        self.assertEqual(
            summary,
            {
                "source_items": 0,
                "total_queried": 0,
                "processed_successfully": 0,
                "failures": 0,
                "status": "completed",
            },
        )
        self.assertEqual(self.snapshot(1), before)

        # Operator force reruns it; the API now succeeds.
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.success_post(requests),
            parent_content_id=1,
            force=True,
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "completed")
        self.assertEqual(row["retry_count"], 0)
        self.assertEqual(row["display_title"], "標題")
        self.assertEqual(row["summary_short"], "這是一段中文翻譯摘要內容。")
        self.assertEqual(row["bullet_1"], "第一要點內容。")
        self.assertEqual(row["bullet_2"], "第二要點內容。")
        self.assertEqual(row["bullet_3"], "第三要點內容。")
        self.assertIsNotNone(row["translated_at"])


class TestFreshCompletedForcedRerun(_OrchestrateBase):
    """Phase 3 items 2-3 / section 7 conclusion 8: forced rerun of a fresh
    completed row (source fingerprint and config still match) is a
    non-persisted execution mode (STATE_TRANSITIONS.md section 2)."""

    def _seed_fresh_completed_row(self) -> None:
        self.seed_record(parent_content_id=1)
        self.seed_translation(
            parent_content_id=1, source_item_id=101, language_code="zh",
            display_title="舊標題", summary_short="這是舊的中文摘要內容。",
            bullet_1="舊第一要點內容。", bullet_2="舊第二要點內容。", bullet_3="舊第三要點內容。",
            source_fingerprint="fp_1", status="completed", retry_count=0,
        )

    def test_forced_rerun_success_overwrites_content_atomically(self) -> None:
        config = _zh_only_config()
        self._seed_fresh_completed_row()

        new_body = _valid_zh_body(
            title="全新標題",
            summary="這是全新的中文摘要內容。",
            bullet_1="新第一要點內容。",
            bullet_2="新第二要點內容。",
            bullet_3="新第三要點內容。",
        )
        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.success_post(requests, zh_body=new_body),
            parent_content_id=1,
            force=True,
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "completed")
        self.assertEqual(row["retry_count"], 0)
        self.assertEqual(row["display_title"], "全新標題")
        self.assertEqual(row["summary_short"], "這是全新的中文摘要內容。")
        self.assertEqual(row["bullet_1"], "新第一要點內容。")
        self.assertEqual(row["bullet_2"], "新第二要點內容。")
        self.assertEqual(row["bullet_3"], "新第三要點內容。")
        self.assertEqual(row["source_fingerprint"], "fp_1")
        # translated_at was refreshed by the successful rerun.
        self.assertNotEqual(row["translated_at"], "2026-06-20T12:00:00Z")
        self.assertIsNotNone(row["translated_at"])

    def test_forced_rerun_api_failure_preserves_completed_row(self) -> None:
        config = _zh_only_config()
        self._seed_fresh_completed_row()
        before = self.snapshot(1)

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.failing_post(requests),
            parent_content_id=1,
            force=True,
        )

        self.assertEqual(summary["processed_successfully"], 0)
        self.assertEqual(summary["failures"], 1)
        # The forced attempt still consumed the per-task retry budget...
        self.assertEqual(len(requests), config.execution_policy.retry_attempts)
        # ...but wrote nothing: the completed row is completely unchanged (no
        # failed write, no retry_count increment, translated_at intact).
        self.assertEqual(self.snapshot(1), before)


class TestStaleRowForcedRerun(_OrchestrateBase):
    """Phase 3 item 3(c): a stale row is NOT protected by the fresh-completed
    forced-rerun model even under --force; it follows the normal stale retry
    path (STATE_TRANSITIONS.md sections 2 and 3)."""

    def _seed_stale_row(self) -> None:
        self.seed_record(parent_content_id=1)
        self.seed_translation(
            parent_content_id=1, source_item_id=101, language_code="zh",
            display_title="過期標題", summary_short="這是過期的中文摘要內容。",
            bullet_1="過期第一要點內容。", bullet_2="過期第二要點內容。", bullet_3="過期第三要點內容。",
            source_fingerprint="fp_1", status="stale", retry_count=1,
        )

    def test_stale_force_failure_uses_normal_stale_retry_path(self) -> None:
        config = _zh_only_config()
        self._seed_stale_row()

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.failing_post(requests),
            parent_content_id=1,
            force=True,
        )

        self.assertEqual(summary["failures"], 1)
        self.assertEqual(len(requests), config.execution_policy.retry_attempts)

        row = self.snapshot(1)
        # Unlike a fresh completed row, the stale row is written failed with
        # retry_count incremented — it is not disguised as current content.
        self.assertEqual(row["translation_status"], "failed")
        self.assertEqual(row["retry_count"], 2)
        # Old content and translated_at are preserved by the failure write.
        self.assertEqual(row["display_title"], "過期標題")
        self.assertEqual(row["summary_short"], "這是過期的中文摘要內容。")
        self.assertEqual(row["bullet_1"], "過期第一要點內容。")
        self.assertEqual(row["bullet_2"], "過期第二要點內容。")
        self.assertEqual(row["bullet_3"], "過期第三要點內容。")
        self.assertEqual(row["translated_at"], "2026-06-20T12:00:00Z")

    def test_stale_force_success_completes_and_resets_retry_count(self) -> None:
        config = _zh_only_config()
        self._seed_stale_row()

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.success_post(requests),
            parent_content_id=1,
            force=True,
        )

        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        row = self.snapshot(1)
        self.assertEqual(row["translation_status"], "completed")
        self.assertEqual(row["retry_count"], 0)
        self.assertEqual(row["display_title"], "標題")
        self.assertEqual(row["summary_short"], "這是一段中文翻譯摘要內容。")
        self.assertNotEqual(row["translated_at"], "2026-06-20T12:00:00Z")


class TestDryRunMode(_OrchestrateBase):
    """Phase 3 item 3 / acceptance: dry_run=True executes the full flow
    including real (mocked) LLM API requests, but persists no database
    writes and skips stale detection marking."""

    def test_dry_run_calls_api_but_persists_nothing(self) -> None:
        config = _zh_only_config()
        self.seed_record(parent_content_id=1)  # no translation row -> eligible
        self.seed_record(parent_content_id=2)
        # Completed row whose model/prompt no longer match the running config:
        # a normal run would mark it stale before queueing.
        self.seed_translation(
            parent_content_id=2, source_item_id=102, language_code="zh",
            display_title="標題二", summary_short="摘要二內容。",
            bullet_1="要點一內容。", bullet_2="要點二內容。", bullet_3="要點三內容。",
            source_fingerprint="fp_2", status="completed", retry_count=0,
            model_name="superseded-model", prompt_version="superseded_prompt_v1",
        )
        config_staled_before = self.snapshot(2)

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests), dry_run=True
        )

        # The mocked API WAS called for the eligible task.
        self.assertEqual(len(requests), 1)
        prompt = requests[0]["messages"][1]["content"]
        self.assertIn("Title: English Title 1\n", prompt)
        self.assertEqual(
            summary,
            {
                "source_items": 1,
                "total_queried": 1,
                "processed_successfully": 1,
                "failures": 0,
                "status": "completed",
            },
        )

        # ...but nothing persisted: no new translation row was written.
        self.assertIsNone(self.snapshot(1))
        # Stale detection did not run: the config-staled row keeps status
        # completed and is byte-for-byte unchanged.
        after = self.snapshot(2)
        self.assertEqual(after, config_staled_before)
        self.assertEqual(after["translation_status"], "completed")


class TestPreviewPromptsMode(_OrchestrateBase):
    """Phase 3 item 3 / acceptance: preview_prompts=True never calls the LLM
    API, writes nothing and skips stale detection."""

    def test_preview_never_calls_api_and_writes_nothing(self) -> None:
        config = _zh_only_config()
        self.seed_record(parent_content_id=1)
        self.seed_record(parent_content_id=2)
        self.seed_translation(
            parent_content_id=2, source_item_id=102, language_code="zh",
            display_title="標題二", summary_short="摘要二內容。",
            bullet_1="要點一內容。", bullet_2="要點二內容。", bullet_3="要點三內容。",
            source_fingerprint="fp_2", status="completed", retry_count=0,
            model_name="superseded-model", prompt_version="superseded_prompt_v1",
        )
        config_staled_before = self.snapshot(2)

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            raise AssertionError(
                "preview-prompts must never issue LLM API requests"
            )

        summary, _ = self.run_orchestrator(
            config=config, fake_post=fake_post, preview_prompts=True
        )

        self.assertEqual(summary["status"], "preview")
        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["previewed"], 1)
        self.assertEqual(summary["failures"], 0)

        self.assertIsNone(self.snapshot(1))
        after = self.snapshot(2)
        self.assertEqual(after, config_staled_before)
        self.assertEqual(after["translation_status"], "completed")


class TestBatchSourceItemSemantics(_OrchestrateBase):
    """Phase 3 item 4 / section 7 conclusion 6: batch_size counts SOURCE
    ITEMS selected by approved_at ASC, parent_content_id ASC; every eligible
    language task of a selected article is expanded (a batch boundary never
    splits one article's pending language set); completed languages are not
    redone."""

    def _seed_three_records(self) -> None:
        for offset, pid in enumerate((1, 2, 3)):
            self.seed_record(
                parent_content_id=pid,
                approved_at=f"2026-06-20T12:0{offset}:00Z",
            )

    def test_batch_size_one_selects_one_article_all_languages(self) -> None:
        config = _zh_ja_config()
        self._seed_three_records()

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests), batch_size=1
        )

        # One source item, both of its language tasks — the boundary never
        # splits one article's pending language set.
        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 2)
        self.assertEqual(summary["processed_successfully"], 2)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(len(requests), 2)

        # The earliest approved article (pid 1) was processed in both languages.
        zh_row = self.snapshot(1, "zh")
        ja_row = self.snapshot(1, "ja")
        self.assertEqual(zh_row["translation_status"], "completed")
        self.assertEqual(zh_row["summary_short"], "這是一段中文翻譯摘要內容。")
        self.assertEqual(ja_row["translation_status"], "completed")
        self.assertEqual(ja_row["summary_short"], "これは翻訳された要約内容です。")
        for pid in (2, 3):
            self.assertIsNone(self.snapshot(pid, "zh"))
            self.assertIsNone(self.snapshot(pid, "ja"))

    def test_batch_size_two_selects_two_articles(self) -> None:
        config = _zh_ja_config()
        self._seed_three_records()

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests), batch_size=2
        )

        self.assertEqual(summary["source_items"], 2)
        self.assertEqual(summary["total_queried"], 4)
        self.assertEqual(summary["processed_successfully"], 4)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(len(requests), 4)
        for pid in (1, 2):
            for lang in ("zh", "ja"):
                row = self.snapshot(pid, lang)
                self.assertIsNotNone(row)
                self.assertEqual(row["translation_status"], "completed")
        self.assertIsNone(self.snapshot(3, "zh"))
        self.assertIsNone(self.snapshot(3, "ja"))

    def test_completed_language_not_redone_within_selected_article(self) -> None:
        config = _zh_ja_config()
        self._seed_three_records()
        # pid 1 zh already completed and fresh (fingerprint + config match).
        self.seed_translation(
            parent_content_id=1, source_item_id=101, language_code="zh",
            display_title="標題一", summary_short="摘要一內容。",
            bullet_1="要點一內容。", bullet_2="要點二內容。", bullet_3="要點三內容。",
            source_fingerprint="fp_1", status="completed", retry_count=0,
        )
        zh_before = self.snapshot(1, "zh")

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests), batch_size=1
        )

        # Only the missing ja task of the selected article ran.
        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(len(requests), 1)
        self.assertIn(f"{JA_LABEL} (ja)", requests[0]["messages"][1]["content"])

        # The fresh completed zh row is byte-for-byte untouched.
        self.assertEqual(self.snapshot(1, "zh"), zh_before)
        ja_row = self.snapshot(1, "ja")
        self.assertEqual(ja_row["translation_status"], "completed")
        self.assertIsNone(self.snapshot(2, "zh"))

    def test_identical_approved_at_tie_breaks_by_parent_content_id(self) -> None:
        config = _zh_ja_config()
        # Insert the higher id first to prove selection is not insertion order.
        self.seed_record(parent_content_id=10, approved_at="2026-06-20T12:00:00Z")
        self.seed_record(parent_content_id=5, approved_at="2026-06-20T12:00:00Z")

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests), batch_size=1
        )

        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 2)
        self.assertEqual(summary["processed_successfully"], 2)
        for lang in ("zh", "ja"):
            row = self.snapshot(5, lang)
            self.assertIsNotNone(row)
            self.assertEqual(row["translation_status"], "completed")
            self.assertIsNone(self.snapshot(10, lang))

    def test_batch_size_above_eligible_records_selects_all(self) -> None:
        # batch_size comes from the config execution policy (no CLI override).
        config = _zh_ja_config(batch_size=10)
        self._seed_three_records()

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests)
        )

        self.assertEqual(summary["source_items"], 3)
        self.assertEqual(summary["total_queried"], 6)
        self.assertEqual(summary["processed_successfully"], 6)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(len(requests), 6)
        for pid in (1, 2, 3):
            for lang in ("zh", "ja"):
                row = self.snapshot(pid, lang)
                self.assertIsNotNone(row)
                self.assertEqual(row["translation_status"], "completed")

    def test_non_positive_batch_size_rejected(self) -> None:
        # Code-review P1 (2026-08-01): --batch-size 0 used to silently process
        # one source item. The orchestrator now validates the effective batch
        # size before any queue loading, stale marking or API traffic.
        config = _zh_ja_config()
        self._seed_three_records()
        for bad in (0, -3):
            with self.subTest(batch_size=bad):
                requests: List[Dict[str, Any]] = []
                with self.assertRaises(ValueError) as ctx:
                    self.run_orchestrator(
                        config=config,
                        fake_post=self.success_post(requests),
                        batch_size=bad,
                    )
                self.assertIn("positive integer", str(ctx.exception))
                self.assertEqual(requests, [])
                self.assertIsNone(self.snapshot(1, "zh"))

    def test_non_integer_batch_size_rejected(self) -> None:
        # Code-review P2 (2026-08-01): batch_size=1.5 used to be accepted by a
        # direct orchestrate_run() caller and selected two source items. The
        # boundary validation now requires an actual positive int, so floats,
        # numeric strings and bools are all rejected before any work begins.
        config = _zh_ja_config()
        self._seed_three_records()
        lock_file = self.workspace / "data" / "translate_runner.lock"
        for bad in (1.5, 2.0, "2", True):
            with self.subTest(batch_size=bad):
                requests: List[Dict[str, Any]] = []
                with self.assertRaises(ValueError) as ctx:
                    self.run_orchestrator(
                        config=config,
                        fake_post=self.success_post(requests),
                        batch_size=bad,
                    )
                self.assertIn("positive integer", str(ctx.exception))
                self.assertEqual(requests, [])
                self.assertIsNone(self.snapshot(1, "zh"))
                self.assertFalse(lock_file.exists())

    def test_single_task_mode_processes_all_languages_of_one_item(self) -> None:
        config = _zh_ja_config()
        self._seed_three_records()

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config, fake_post=self.success_post(requests), parent_content_id=2
        )

        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 2)
        self.assertEqual(summary["processed_successfully"], 2)
        self.assertEqual(len(requests), 2)
        for lang in ("zh", "ja"):
            row = self.snapshot(2, lang)
            self.assertIsNotNone(row)
            self.assertEqual(row["translation_status"], "completed")
        for pid in (1, 3):
            self.assertIsNone(self.snapshot(pid, "zh"))
            self.assertIsNone(self.snapshot(pid, "ja"))

    def test_single_task_mode_with_language_filter(self) -> None:
        config = _zh_ja_config()
        self._seed_three_records()

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.success_post(requests),
            parent_content_id=2,
            language_code="ja",
        )

        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(len(requests), 1)
        self.assertIn(f"{JA_LABEL} (ja)", requests[0]["messages"][1]["content"])
        self.assertIsNone(self.snapshot(2, "zh"))
        ja_row = self.snapshot(2, "ja")
        self.assertEqual(ja_row["translation_status"], "completed")


class TestRunSummaryContract(_OrchestrateBase):
    """Phase 3 items 3-4: the run summary reports source item and language
    task counts plus the partial-failure outcome on a mixed run."""

    def test_mixed_outcome_summary_counts(self) -> None:
        config = _zh_only_config()
        self.seed_record(parent_content_id=1)  # API fails for this item
        self.seed_record(parent_content_id=2)  # API succeeds

        requests: List[Dict[str, Any]] = []
        summary, _ = self.run_orchestrator(
            config=config,
            fake_post=self.failing_post(requests, fail_marker="Title: English Title 1\n"),
            batch_size=10,
        )

        self.assertEqual(summary["source_items"], 2)
        self.assertEqual(summary["total_queried"], 2)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["status"], "completed")

        # The failing task exhausted its retry budget; the succeeding task
        # needed exactly one request.
        self.assertEqual(len(requests), config.execution_policy.retry_attempts + 1)

        failed_row = self.snapshot(1)
        self.assertEqual(failed_row["translation_status"], "failed")
        self.assertEqual(failed_row["retry_count"], 1)
        ok_row = self.snapshot(2)
        self.assertEqual(ok_row["translation_status"], "completed")


if __name__ == "__main__":
    unittest.main()
