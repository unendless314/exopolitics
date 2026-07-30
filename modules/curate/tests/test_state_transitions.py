import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from modules.curate.src.database import (
    CurationRepository,
    get_connection,
    run_migrations,
)
from modules.curate.src.orchestrator import curate_item
from modules.curate.tests.support import (
    CURATE_MIGRATIONS_DIR,
    FakeHTTPClient,
    build_test_config,
    create_mock_upstream_tables,
    make_chat_completion_payload,
    make_mock_http_response,
    make_temp_workspace,
    make_valid_response,
    seed_curation_state,
    seed_upstream_item,
    snapshot_curate_tables,
)

PUBLISH_ACTIONS = ("publish_link", "publish_summary")
REJECT_ACTIONS = ("edit_rewrite", "reject_discard")
ALL_ACTIONS = PUBLISH_ACTIONS + REJECT_ACTIONS


class TestStateTransitions(unittest.TestCase):
    """STATE_TRANSITIONS.md matrix coverage via curate_item with a mocked LLM.

    Each sub-test locks exactly one transition and its side effects: the
    decision row plus the presence/absence/content of editor_brief and
    curation_output rows.
    """

    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        self.db_path = self.workspace / "data" / "canonical.db"
        create_mock_upstream_tables(self.db_path)
        run_migrations(self.db_path, CURATE_MIGRATIONS_DIR)
        self.conn = get_connection(self.db_path)
        self.addCleanup(self.conn.close)
        self.config = build_test_config(
            supports_structured_output=False, retry_attempts=2, backoff_factor=0.1
        )
        self._id_counter = [100]

    # --- helpers ---

    def _new_item(self):
        item_id = self._id_counter[0]
        self._id_counter[0] += 1
        seed_upstream_item(
            self.conn, item_id, title=f"Item {item_id}", text="body", topic_class="core"
        )
        return {
            "source_item_id": item_id,
            "raw_title": f"Item {item_id}",
            "sanitized_text": "body",
            "canonical_url": None,
            "topic_class": "core",
            "classification_reason": "seeded",
            "governmental_involvement": 1,
        }

    def _run_curate(self, item, response_action=None, fail_with_status=None):
        if fail_with_status is not None:
            outcome = make_mock_http_response(status_code=fail_with_status, text="server error")
        else:
            outcome = make_mock_http_response(
                status_code=200,
                json_data=make_chat_completion_payload(make_valid_response(response_action)),
            )
        db_lock = asyncio.Lock()

        async def run():
            return await curate_item(
                repo=CurationRepository(self.conn),
                client=FakeHTTPClient([outcome]),
                config=self.config,
                item=item,
                api_key="dummy",
                db_lock=db_lock,
                commit=True,
            )

        with patch("asyncio.sleep", new=AsyncMock()):
            return asyncio.run(run())

    def _row(self, table, item_id):
        cursor = self.conn.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE source_item_id = ?", (item_id,))
        return cursor.fetchone()

    def _assert_side_rows(self, item_id, action):
        """Locks the validation-matrix side rows for a successful outcome."""
        brief = self._row("editor_brief", item_id)
        output = self._row("curation_output", item_id)

        if action in PUBLISH_ACTIONS:
            self.assertIsNotNone(brief)
            expected_format = "link_card" if action == "publish_link" else "structured_summary"
            self.assertEqual(brief["target_format"], expected_format)
            self.assertIsNotNone(output)
            bullets = [output["bullet_1"], output["bullet_2"], output["bullet_3"]]
            if action == "publish_link":
                self.assertEqual(bullets, [None, None, None])
            else:
                self.assertTrue(all(isinstance(b, str) and b.strip() for b in bullets))
        elif action == "edit_rewrite":
            self.assertIsNotNone(brief)
            self.assertIsNone(output)
        else:  # reject_discard
            self.assertIsNone(brief)
            self.assertIsNone(output)

    # --- transitions ---

    def test_pending_to_all_target_states(self):
        for action in ALL_ACTIONS:
            with self.subTest(action=action):
                item = self._new_item()
                self.assertTrue(self._run_curate(item, response_action=action))

                dec = self._row("curation_decision", item["source_item_id"])
                expected_status = "approved" if action in PUBLISH_ACTIONS else "rejected"
                self.assertEqual(dec["curate_status"], expected_status)
                self.assertEqual(dec["downstream_action"], action)
                self.assertEqual(dec["retry_count"], 0)
                self.assertEqual(dec["decision_actor"], "system")
                self._assert_side_rows(item["source_item_id"], action)

    def test_failed_retry_success_resets_retry_count(self):
        # Normal queue: a failed item later succeeding resets retry_count to 0
        # and builds the correct brief/output for its new action.
        for action in ALL_ACTIONS:
            with self.subTest(action=action):
                item = self._new_item()
                seed_curation_state(
                    self.conn, item["source_item_id"],
                    curate_status="failed", downstream_action=None,
                    retry_count=1, decision_reason="prior transient failure",
                )
                self.assertTrue(self._run_curate(item, response_action=action))

                dec = self._row("curation_decision", item["source_item_id"])
                expected_status = "approved" if action in PUBLISH_ACTIONS else "rejected"
                self.assertEqual(dec["curate_status"], expected_status)
                self.assertEqual(dec["downstream_action"], action)
                self.assertEqual(dec["retry_count"], 0)
                self.assertNotEqual(dec["decision_reason"], "prior transient failure")
                self._assert_side_rows(item["source_item_id"], action)

    def test_publish_link_to_publish_summary_and_back(self):
        # publish_link -> publish_summary: bullets are populated.
        item = self._new_item()
        seed_curation_state(
            self.conn, item["source_item_id"],
            curate_status="approved", downstream_action="publish_link",
            with_brief=True, with_output=True,
        )
        self.assertTrue(self._run_curate(item, response_action="publish_summary"))
        dec = self._row("curation_decision", item["source_item_id"])
        self.assertEqual(dec["downstream_action"], "publish_summary")
        self._assert_side_rows(item["source_item_id"], "publish_summary")

        # publish_summary -> publish_link: bullets are nulled out on the row.
        item = self._new_item()
        seed_curation_state(
            self.conn, item["source_item_id"],
            curate_status="approved", downstream_action="publish_summary",
            with_brief=True, with_output=True,
        )
        self.assertTrue(self._run_curate(item, response_action="publish_link"))
        output = self._row("curation_output", item["source_item_id"])
        self.assertEqual(
            (output["bullet_1"], output["bullet_2"], output["bullet_3"]),
            (None, None, None),
        )
        brief = self._row("editor_brief", item["source_item_id"])
        self.assertEqual(brief["target_format"], "link_card")

    def test_edit_rewrite_to_publish_actions_rebuilds_output(self):
        for action in PUBLISH_ACTIONS:
            with self.subTest(action=action):
                item = self._new_item()
                seed_curation_state(
                    self.conn, item["source_item_id"],
                    curate_status="rejected", downstream_action="edit_rewrite",
                    with_brief=True, with_output=False,
                )
                self.assertTrue(self._run_curate(item, response_action=action))

                dec = self._row("curation_decision", item["source_item_id"])
                self.assertEqual(dec["curate_status"], "approved")
                self.assertEqual(dec["downstream_action"], action)
                # A fresh output row is created (it did not exist before).
                self._assert_side_rows(item["source_item_id"], action)
                brief = self._row("editor_brief", item["source_item_id"])
                self.assertNotEqual(brief["brief_goal"], "SEEDED brief goal")

    def test_completed_states_to_edit_rewrite_cleans_stale_output(self):
        pre_states = [
            ("approved", "publish_summary", True, True),
            ("rejected", "edit_rewrite", True, False),
            ("withdrawn", "publish_link", True, True),
        ]
        for status, action, with_brief, with_output in pre_states:
            with self.subTest(old_state=(status, action)):
                item = self._new_item()
                seed_curation_state(
                    self.conn, item["source_item_id"],
                    curate_status=status, downstream_action=action,
                    with_brief=with_brief, with_output=with_output,
                    decision_actor="operator" if status == "withdrawn" else "system",
                )
                self.assertTrue(self._run_curate(item, response_action="edit_rewrite"))

                dec = self._row("curation_decision", item["source_item_id"])
                self.assertEqual(dec["curate_status"], "rejected")
                self.assertEqual(dec["downstream_action"], "edit_rewrite")
                self.assertEqual(dec["decision_actor"], "system")
                # Brief updated with new LLM content; output must be gone.
                brief = self._row("editor_brief", item["source_item_id"])
                self.assertIsNotNone(brief)
                self.assertNotEqual(brief["brief_goal"], "SEEDED brief goal")
                self.assertIsNone(self._row("curation_output", item["source_item_id"]))

    def test_completed_states_to_reject_discard_cleans_all_side_rows(self):
        pre_states = [
            ("approved", "publish_summary"),
            ("rejected", "edit_rewrite"),
            ("withdrawn", "publish_link"),
        ]
        for status, action in pre_states:
            with self.subTest(old_state=(status, action)):
                item = self._new_item()
                seed_curation_state(
                    self.conn, item["source_item_id"],
                    curate_status=status, downstream_action=action,
                    with_brief=True, with_output=(status != "rejected"),
                    decision_actor="operator" if status == "withdrawn" else "system",
                )
                self.assertTrue(self._run_curate(item, response_action="reject_discard"))

                dec = self._row("curation_decision", item["source_item_id"])
                self.assertEqual(dec["curate_status"], "rejected")
                self.assertEqual(dec["downstream_action"], "reject_discard")
                self.assertEqual(dec["decision_actor"], "system")
                self.assertIsNone(self._row("editor_brief", item["source_item_id"]))
                self.assertIsNone(self._row("curation_output", item["source_item_id"]))

    def test_forced_rerun_failure_preserves_completed_state_completely(self):
        pre_states = [
            ("approved", "publish_summary"),
            ("withdrawn", "publish_link"),
        ]
        for status, action in pre_states:
            with self.subTest(old_state=(status, action)):
                item = self._new_item()
                seed_curation_state(
                    self.conn, item["source_item_id"],
                    curate_status=status, downstream_action=action,
                    with_brief=True, with_output=True,
                    decision_actor="operator" if status == "withdrawn" else "system",
                    decision_reason=f"seeded {status} reason",
                )
                before = snapshot_curate_tables(self.conn)

                success = self._run_curate(item, fail_with_status=503)
                self.assertFalse(success)

                # Snapshot-level proof: all three curate tables are completely
                # unchanged (decision, brief, and output, every column).
                after = snapshot_curate_tables(self.conn)
                self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
