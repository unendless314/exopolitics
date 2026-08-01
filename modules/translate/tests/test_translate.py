"""Public journey tests for the translate module (orchestrator + assembler).

TRANSLATE_TEST_MAINTAINABILITY_PLAN Phase 4 item 4: the legacy wide-scenario
module tests are converged into a few end-to-end journeys through the public
orchestrate_run() / assemble_approved_content_records() surface. Precise
low-level rules live in test_five_field_contract.py; state/queue/batch/force
matrices live in test_state.py; the CLI surface lives in test_cli.py; the
real-schema handoff contract lives in test_handoff_contract.py.

Journeys:
1. assemble -> preview -> run (preview never calls the API; the run writes a
   provider-model completed row, pinning the non-bypass write direction).
2. upstream edit -> re-assemble -> stale detection retranslates in the same
   run (retry_count stays 0 on the stale retry path).
3. force rerun of a fresh completed row: API failure leaves the row
   byte-for-byte unchanged; success atomically overwrites the five fields and
   refreshes translated_at.

Deterministic: httpx.AsyncClient.post is patched, asyncio.sleep and
random.uniform are patched around every orchestrate_run call, the workspace
DB lives in a temporary directory (the process lock stays inside it), the API
key comes from a patched TEST_TRANSLATE_API_KEY, no .env reads, no real HTTP.
"""

import asyncio
import itertools
import json
import pathlib
import tempfile
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from modules.translate.src.approved_content_record import (
    assemble_approved_content_records,
)
from modules.translate.src.database import get_connection
from modules.translate.src.orchestrator import orchestrate_run
from modules.translate.tests import support

ZH_LABEL = "Traditional Chinese"

SOURCE_TITLE = "UAP Hearing Summary"
SOURCE_SUMMARY = "This is a brief summary content."
SOURCE_BULLET_1 = "Claim content."
SOURCE_BULLET_2 = "Evidence content."
SOURCE_BULLET_3 = "Impact content."

# Valid zh response bodies (CJK present, aggregate ratio well below the
# locked 5.0 limit).
BODY_A = support.make_chat_completion_body(
    support.make_five_field_response(
        title="中文標題甲",
        summary="這是一段中文翻譯摘要內容。",
        bullet_1="第一要點內容。",
        bullet_2="第二要點內容。",
        bullet_3="第三要點內容。",
    )
)
BODY_B = support.make_chat_completion_body(
    support.make_five_field_response(
        title="中文標題乙",
        summary="這是更新後的中文翻譯摘要內容。",
        bullet_1="新第一要點內容。",
        bullet_2="新第二要點內容。",
        bullet_3="新第三要點內容。",
    )
)


def _fields_of(body: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts the five translated fields from a chat.completion body."""
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)


class TestTranslateJourneys(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = pathlib.Path(self.temp_dir.name)
        self.db_path = support.build_temp_workspace(self.workspace)
        # zh-only target set; 5.0 is the locked production ratio policy.
        self.config = support.build_mock_config(
            target_languages={
                "zh": support.make_target_language(label=ZH_LABEL, max_title_length=120)
            },
            supports_structured_output=False,
            content_ratio_limit=5.0,
        )
        # Deterministic monotonic clock: get_utc_now_iso8601() has one-second
        # resolution, so real wall-clock time cannot distinguish two runs made
        # within the same second. Each call advances one second, making
        # translated_at refresh assertions deterministic.
        self._clock_ticks = itertools.count()

    # -- helpers -------------------------------------------------------------

    def seed_summary_approval(self, *, source_item_id: int = 10, updated_at: str = "2026-06-20T12:00:00Z") -> None:
        conn = get_connection(self.db_path)
        try:
            support.seed_curation_approval(
                conn,
                source_item_id=source_item_id,
                downstream_action="publish_summary",
                display_title=SOURCE_TITLE,
                summary_short=SOURCE_SUMMARY,
                bullet_1=SOURCE_BULLET_1,
                bullet_2=SOURCE_BULLET_2,
                bullet_3=SOURCE_BULLET_3,
                updated_at=updated_at,
            )
        finally:
            conn.close()

    def assemble(self) -> Dict[str, Any]:
        conn = get_connection(self.db_path)
        try:
            return assemble_approved_content_records(conn)
        finally:
            conn.close()

    def approved_record(self, *, source_item_id: int = 10) -> Optional[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        try:
            return support.snapshot_approved_record(conn, source_item_id=source_item_id)
        finally:
            conn.close()

    def translation_row(self, *, parent_content_id: int) -> Optional[Dict[str, Any]]:
        conn = get_connection(self.db_path)
        try:
            return support.snapshot_translation_row(
                conn, parent_content_id=parent_content_id, language_code="zh"
            )
        finally:
            conn.close()

    def run_orchestrator(self, *, fake_post, **run_kwargs) -> Dict[str, Any]:
        """Runs orchestrate_run with env, no-wait, clock and HTTP patches.

        asyncio.sleep (retry backoff and rate-limit stagger) and the jitter
        source are patched so no path ever actually waits; the orchestrator's
        get_utc_now_iso8601 is replaced by a deterministic monotonic clock.
        Returns the run summary dict.
        """

        def fake_now() -> str:
            tick = next(self._clock_ticks)
            return f"2026-08-01T00:{tick // 60:02d}:{tick % 60:02d}Z"

        async def fake_sleep(duration: float) -> None:
            return None

        with patch.dict(
            "os.environ", {"TEST_TRANSLATE_API_KEY": "test-key"}
        ), patch(
            "modules.translate.src.orchestrator.asyncio.sleep", new=fake_sleep
        ), patch(
            "modules.translate.src.orchestrator.random.uniform", return_value=0.0
        ), patch(
            "modules.translate.src.orchestrator.get_utc_now_iso8601", new=fake_now
        ), patch(
            "httpx.AsyncClient.post", new=fake_post
        ):
            return asyncio.run(
                orchestrate_run(config=self.config, db_path=self.db_path, **run_kwargs)
            )

    @staticmethod
    def ok_post(requests: List[Dict[str, Any]], body: Dict[str, Any]):
        """fake_post returning one fixed valid zh body for every request."""

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            requests.append(json)
            return support.make_http_response(200, body)

        return fake_post

    @staticmethod
    def failing_post(requests: List[Dict[str, Any]]):
        """fake_post returning HTTP 500 (retryable) for every request."""

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            requests.append(json)
            return support.make_http_response(500)

        return fake_post

    # -- journeys ------------------------------------------------------------

    def test_journey_assemble_preview_then_run(self) -> None:
        self.seed_summary_approval()

        stats = self.assemble()
        self.assertEqual(stats["inserted"], 1)
        record = self.approved_record()
        self.assertIsNotNone(record)
        parent_content_id = record["parent_content_id"]

        # Preview: no API calls, no translation rows, summary status 'preview'.
        async def must_not_call_api(self, url, headers=None, json=None, timeout=None):
            raise AssertionError("preview-prompts must never issue LLM API requests")

        preview_summary = self.run_orchestrator(
            fake_post=must_not_call_api, preview_prompts=True
        )
        self.assertEqual(preview_summary["status"], "preview")
        self.assertEqual(preview_summary["source_items"], 1)
        self.assertEqual(preview_summary["total_queried"], 1)
        self.assertIsNone(self.translation_row(parent_content_id=parent_content_id))

        # Run: exactly one API call, one source item, one success.
        requests: List[Dict[str, Any]] = []
        summary = self.run_orchestrator(fake_post=self.ok_post(requests, BODY_A))
        self.assertEqual(len(requests), 1)
        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        row = self.translation_row(parent_content_id=parent_content_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["translation_status"], "completed")
        fields = _fields_of(BODY_A)
        self.assertEqual(row["display_title"], fields["translated_title"])
        self.assertEqual(row["summary_short"], fields["translated_summary"])
        self.assertEqual(row["bullet_1"], fields["translated_bullet_1"])
        self.assertEqual(row["bullet_2"], fields["translated_bullet_2"])
        self.assertEqual(row["bullet_3"], fields["translated_bullet_3"])
        self.assertEqual(row["source_fingerprint"], record["content_fingerprint"])
        # Non-bypass direction: API-written rows carry the provider model, not
        # the 'bypass' marker (previously pinned by the legacy bypass-policy
        # scenario test).
        self.assertEqual(row["model_name"], self.config.active_provider.model_name)
        self.assertNotEqual(row["model_name"], "bypass")
        self.assertEqual(row["prompt_version"], self.config.active_template.version)

    def test_journey_upstream_edit_stale_then_retranslate(self) -> None:
        self.seed_summary_approval(updated_at="2026-06-20T12:00:00Z")
        self.assemble()
        record = self.approved_record()
        parent_content_id = record["parent_content_id"]

        # First run completes the zh translation with body A.
        requests: List[Dict[str, Any]] = []
        summary = self.run_orchestrator(fake_post=self.ok_post(requests, BODY_A))
        self.assertEqual(summary["processed_successfully"], 1)
        row_a = self.translation_row(parent_content_id=parent_content_id)
        self.assertEqual(row_a["translation_status"], "completed")

        # Upstream edit: new display_title with a newer updated_at marker.
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE curation_output SET display_title = 'Edited Hearing Summary', "
                "updated_at = '2026-06-21T00:00:00Z' WHERE source_item_id = 10"
            )
            conn.commit()
        finally:
            conn.close()

        # Re-assembly updates the handoff row and changes its fingerprint.
        stats = self.assemble()
        self.assertEqual(stats["updated"], 1)
        edited_record = self.approved_record()
        self.assertEqual(edited_record["display_title"], "Edited Hearing Summary")
        self.assertNotEqual(
            edited_record["content_fingerprint"], record["content_fingerprint"]
        )

        # The next run's stale detection marks the zh row stale and
        # retranslates it in the same run (normal stale retry path).
        requests = []
        summary = self.run_orchestrator(fake_post=self.ok_post(requests, BODY_B))
        self.assertEqual(summary["source_items"], 1)
        self.assertEqual(summary["total_queried"], 1)
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(len(requests), 1)

        row_b = self.translation_row(parent_content_id=parent_content_id)
        self.assertEqual(row_b["translation_status"], "completed")
        fields_b = _fields_of(BODY_B)
        self.assertEqual(row_b["display_title"], fields_b["translated_title"])
        self.assertEqual(row_b["summary_short"], fields_b["translated_summary"])
        self.assertEqual(row_b["bullet_1"], fields_b["translated_bullet_1"])
        self.assertEqual(row_b["bullet_2"], fields_b["translated_bullet_2"])
        self.assertEqual(row_b["bullet_3"], fields_b["translated_bullet_3"])
        self.assertEqual(row_b["source_fingerprint"], edited_record["content_fingerprint"])
        # Successful stale retranslation resets the retry count.
        self.assertEqual(row_b["retry_count"], 0)

    def test_journey_force_rerun_failure_then_success(self) -> None:
        self.seed_summary_approval()
        self.assemble()
        record = self.approved_record()
        parent_content_id = record["parent_content_id"]

        # Baseline completed row via a normal run with body A.
        requests: List[Dict[str, Any]] = []
        summary = self.run_orchestrator(fake_post=self.ok_post(requests, BODY_A))
        self.assertEqual(summary["processed_successfully"], 1)
        before = self.translation_row(parent_content_id=parent_content_id)
        self.assertEqual(before["translation_status"], "completed")

        # Forced rerun with the API always failing (HTTP 500): the summary
        # reports the failure and the completed row is COMPLETELY unchanged.
        requests = []
        summary = self.run_orchestrator(
            fake_post=self.failing_post(requests),
            parent_content_id=parent_content_id,
            force=True,
        )
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["processed_successfully"], 0)
        after_failure = self.translation_row(parent_content_id=parent_content_id)
        self.assertEqual(after_failure, before)

        # Forced rerun again with a fresh valid body: the five fields are
        # atomically overwritten, retry_count stays 0 and translated_at is
        # refreshed.
        requests = []
        summary = self.run_orchestrator(
            fake_post=self.ok_post(requests, BODY_B),
            parent_content_id=parent_content_id,
            force=True,
        )
        self.assertEqual(summary["processed_successfully"], 1)
        self.assertEqual(summary["failures"], 0)

        after_success = self.translation_row(parent_content_id=parent_content_id)
        self.assertEqual(after_success["translation_status"], "completed")
        fields_b = _fields_of(BODY_B)
        self.assertEqual(after_success["display_title"], fields_b["translated_title"])
        self.assertEqual(after_success["summary_short"], fields_b["translated_summary"])
        self.assertEqual(after_success["bullet_1"], fields_b["translated_bullet_1"])
        self.assertEqual(after_success["bullet_2"], fields_b["translated_bullet_2"])
        self.assertEqual(after_success["bullet_3"], fields_b["translated_bullet_3"])
        self.assertEqual(after_success["retry_count"], 0)
        self.assertNotEqual(after_success["translated_at"], before["translated_at"])


if __name__ == "__main__":
    unittest.main()
