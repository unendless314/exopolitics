import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from modules.curate.src.database import get_connection, run_migrations
from modules.curate.src.orchestrator import DispatchPacer, orchestrate_run
from modules.curate.tests.support import (
    CURATE_MIGRATIONS_DIR,
    build_test_config,
    create_mock_upstream_tables,
    make_chat_completion_payload,
    make_mock_http_response,
    make_temp_workspace,
    make_valid_response,
    seed_upstream_item,
)


class TestExecutionPolicy(unittest.TestCase):
    """Orchestrate-level execution rules: dispatch-time rate pacing,
    semaphore concurrency, and per-item failure isolation with retry
    accumulation."""

    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        self.db_path = self.workspace / "data" / "canonical.db"
        create_mock_upstream_tables(self.db_path)
        run_migrations(self.db_path, CURATE_MIGRATIONS_DIR)

        self.env_patch = patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _seed_items(self, count: int) -> None:
        conn = get_connection(self.db_path)
        try:
            for item_id in range(1, count + 1):
                seed_upstream_item(
                    conn, item_id,
                    title=f"Item {item_id} title",
                    text=f"Item {item_id} body",
                    topic_class="core",
                )
        finally:
            conn.close()

    def _fetch_decision(self, item_id: int):
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM curation_decision WHERE source_item_id = ?", (item_id,))
            return cursor.fetchone()
        finally:
            conn.close()

    def _run_pacing_scenario(self, post_latency: float):
        """Runs a batch against a fake clock with simulated provider latency
        and records the dispatch start time of every HTTP request.

        Deterministic by construction: the pacer's clock only advances via
        the injected sleep and the simulated latency, so no real waiting or
        timing tolerances are involved.
        """
        self._seed_items(4)
        config = build_test_config(
            supports_structured_output=False,
            rate_limit_per_minute=60,
            max_concurrent_requests=2,
        )
        clock = [0.0]
        starts = []

        async def fake_sleep(delay: float) -> None:
            clock[0] += delay

        async def latency_post(self, *args, **kwargs):
            starts.append(clock[0])
            # Real yield so workers genuinely overlap and queue on the
            # semaphore; the fake clock only advances via the deterministic
            # knobs (injected sleep and simulated latency), never via waits.
            await asyncio.sleep(0)
            clock[0] += post_latency
            return make_mock_http_response(
                status_code=200,
                json_data=make_chat_completion_payload(make_valid_response("reject_discard")),
            )

        def pacer_with_fake_time(rpm):
            return DispatchPacer(rpm, clock=lambda: clock[0], sleep=fake_sleep)

        with patch("httpx.AsyncClient.post", latency_post), \
             patch(
                 "modules.curate.src.orchestrator.DispatchPacer",
                 side_effect=pacer_with_fake_time,
             ):
            summary = asyncio.run(orchestrate_run(config, self.db_path))

        self.assertEqual(summary["processed_successfully"], 4)
        return config, starts

    def _assert_dispatch_gaps(self, config, starts) -> None:
        self.assertEqual(len(starts), 4)
        # Expectation derived from the config object, never hardcoded.
        interval = 60.0 / config.execution_policy.rate_limit_per_minute
        ordered = sorted(starts)
        for prev, cur in zip(ordered, ordered[1:]):
            self.assertGreaterEqual(cur - prev, interval - 1e-9)

    def test_dispatch_pacing_spaces_request_starts_under_fast_provider(self):
        config, starts = self._run_pacing_scenario(post_latency=0.0)
        self._assert_dispatch_gaps(config, starts)

    def test_dispatch_pacing_spaces_request_starts_under_slow_provider(self):
        # Slow responses must not allow the shared pacer to shorten dispatch
        # gaps. The event-loop-stall test below separately covers stale
        # pre-reserved slots waking together after queued workers are released.
        config, starts = self._run_pacing_scenario(post_latency=2.5)
        self._assert_dispatch_gaps(config, starts)

    def test_dispatch_pacing_holds_gaps_after_event_loop_stall(self):
        # Regression coverage for the event-loop-delay variant: sleeps that
        # expire during a loop stall all complete in the same iteration, so
        # workers must not dispatch on stale pre-reserved slots. The harness
        # stalls the fake clock and then releases ALL expired sleepers at the
        # same instant on every iteration; spacing must survive anyway.
        self._seed_items(4)
        config = build_test_config(
            supports_structured_output=False,
            rate_limit_per_minute=60,
            max_concurrent_requests=4,
        )
        clock = [0.0]
        starts = []
        waiters = []  # events for workers currently parked in a pacer sleep

        async def fake_sleep(delay: float) -> None:
            ev = asyncio.Event()
            waiters.append(ev)
            await ev.wait()

        async def instant_post(self, *args, **kwargs):
            starts.append(clock[0])
            return make_mock_http_response(
                status_code=200,
                json_data=make_chat_completion_payload(make_valid_response("reject_discard")),
            )

        def pacer_with_fake_time(rpm):
            return DispatchPacer(rpm, clock=lambda: clock[0], sleep=fake_sleep)

        async def scenario():
            with patch("httpx.AsyncClient.post", instant_post), \
                 patch(
                     "modules.curate.src.orchestrator.DispatchPacer",
                     side_effect=pacer_with_fake_time,
                 ):
                task = asyncio.create_task(orchestrate_run(config, self.db_path))
                while not task.done():
                    if waiters:
                        # One stall wakes every expired sleeper together.
                        clock[0] += 5.0
                        pending, waiters[:] = waiters[:], []
                        for ev in pending:
                            ev.set()
                    await asyncio.sleep(0)
                return await task

        summary = asyncio.run(scenario())
        self.assertEqual(summary["processed_successfully"], 4)
        self._assert_dispatch_gaps(config, starts)

    def test_semaphore_limits_in_flight_requests(self):
        self._seed_items(6)
        config = build_test_config(
            supports_structured_output=False,
            max_concurrent_requests=3,
            # High rpm keeps pacer spacing negligible so the in-flight window
            # is driven by the semaphore, not the pace schedule.
            rate_limit_per_minute=60000,
        )
        state = {"current": 0, "peak": 0}

        async def tracked_post(self, *args, **kwargs):
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.02)  # real yield so tasks genuinely overlap
            state["current"] -= 1
            return make_mock_http_response(
                status_code=200,
                json_data=make_chat_completion_payload(make_valid_response("reject_discard")),
            )

        with patch("httpx.AsyncClient.post", tracked_post):
            summary = asyncio.run(orchestrate_run(config, self.db_path))

        self.assertEqual(summary["processed_successfully"], 6)
        self.assertLessEqual(state["peak"], config.execution_policy.max_concurrent_requests)
        self.assertGreaterEqual(state["peak"], 2)  # proves overlap really happened

    def test_failure_isolation_and_retry_count_accumulation(self):
        conn = get_connection(self.db_path)
        try:
            seed_upstream_item(conn, 1, title="FAIL ITEM title", text="body", topic_class="core")
            seed_upstream_item(conn, 2, title="OK ITEM title", text="body", topic_class="core")
        finally:
            conn.close()

        config = build_test_config(
            supports_structured_output=False, retry_attempts=2, backoff_factor=0.1
        )

        async def routed_post(self, url, headers=None, json=None, timeout=None):
            user_content = json["messages"][1]["content"]
            if "FAIL ITEM" in user_content:
                return make_mock_http_response(status_code=503, text="server error")
            return make_mock_http_response(
                status_code=200,
                json_data=make_chat_completion_payload(make_valid_response("publish_summary")),
            )

        # First run: item 1 fails, item 2 succeeds; both outcomes isolated.
        with patch("httpx.AsyncClient.post", routed_post), \
             patch("asyncio.sleep", new=AsyncMock()):
            summary1 = asyncio.run(orchestrate_run(config, self.db_path))

        self.assertEqual(summary1["total_queried"], 2)
        self.assertEqual(summary1["processed_successfully"], 1)
        self.assertEqual(summary1["failures"], 1)

        dec1 = self._fetch_decision(1)
        self.assertEqual(dec1["curate_status"], "failed")
        self.assertIsNone(dec1["downstream_action"])
        self.assertEqual(dec1["retry_count"], 1)
        self.assertIn("503", dec1["decision_reason"])

        dec2 = self._fetch_decision(2)
        self.assertEqual(dec2["curate_status"], "approved")
        self.assertEqual(dec2["downstream_action"], "publish_summary")
        self.assertEqual(dec2["retry_count"], 0)

        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM editor_brief WHERE source_item_id = 2")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute("SELECT 1 FROM curation_output WHERE source_item_id = 2")
            self.assertIsNotNone(cursor.fetchone())
        finally:
            conn.close()

        # Second run: only the failed item re-enters the queue and its retry
        # count accumulates; the approved item is not re-processed.
        with patch("httpx.AsyncClient.post", routed_post), \
             patch("asyncio.sleep", new=AsyncMock()):
            summary2 = asyncio.run(orchestrate_run(config, self.db_path))

        self.assertEqual(summary2["total_queried"], 1)
        self.assertEqual(summary2["failures"], 1)

        dec1_second = self._fetch_decision(1)
        self.assertEqual(dec1_second["curate_status"], "failed")
        self.assertEqual(dec1_second["retry_count"], 2)

        dec2_second = self._fetch_decision(2)
        self.assertEqual(dec2_second["curate_status"], "approved")
        self.assertEqual(dec2_second["retry_count"], 0)


class TestDispatchPacer(unittest.TestCase):
    """Slot math for the shared dispatch-time rate limiter, made
    deterministic with injected clock/sleep."""

    def test_slots_are_spaced_and_reanchor_after_idle(self):
        clock = [100.0]
        sleeps = []

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)
            clock[0] += delay

        rpm = 60
        interval = 60.0 / rpm

        async def run():
            pacer = DispatchPacer(rpm, clock=lambda: clock[0], sleep=fake_sleep)
            await pacer.wait()  # first slot starts immediately, no sleep
            await pacer.wait()  # second slot waits exactly one interval
            await pacer.wait()  # third slot waits exactly one interval
            clock[0] += 1000.0  # long idle gap (e.g. provider stall)
            await pacer.wait()  # re-anchors to now: no catch-up burst

        asyncio.run(run())
        self.assertEqual(sleeps, [interval, interval])

    def test_non_positive_rate_disables_pacing(self):
        for rpm in (0, -30):
            with self.subTest(rpm=rpm):
                mock_sleep = AsyncMock()

                async def run():
                    pacer = DispatchPacer(rpm, clock=lambda: 0.0, sleep=mock_sleep)
                    await pacer.wait()

                asyncio.run(run())
                mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
