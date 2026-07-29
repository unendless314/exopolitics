"""Integration tests for orchestrator operational contracts.

Covers the non-force execution paths that test_integration.py (which mostly
runs with force=True) does not prove: 304 cache-hit handling, run-scope
filtering, skip paths (not_due / quarantined / disabled), the force bypass,
dry-run write isolation, the consecutive-failure quarantine cycle, and the
bozo -> parse_error source-level failure contract.

Every test uses a temporary config directory, a temporary migrated SQLite DB,
a mocked fetch_feed, and a patched orchestrator clock (FIXED_NOW). No test
touches the real network, the real clock, or real source data.
"""

import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from modules.ingest.src.config import validate_and_load_config
from modules.ingest.src.database import get_connection, run_migrations
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src.orchestrator import orchestrate_run, orchestrate_source
from modules.ingest.tests import feed_samples

# All orchestrator-visible timestamps are pinned to this value by patching
# modules.ingest.src.orchestrator.get_utc_now_iso8601.
FIXED_NOW = "2026-07-01T00:00:00Z"


def _sources_yaml(sources_block: str) -> str:
    """Composes a minimal valid sources.yaml around the given sources block."""
    return (
        "schema_version: 1\n"
        "schedule_classes:\n"
        "  daily:\n"
        "    target_interval_minutes: 1440\n"
        "    description: Daily\n"
        "sanitization_profiles:\n"
        "  default_html_article:\n"
        "    input_preference: [summary]\n"
        "    decode_entities: true\n"
        "    remove_selectors: [script]\n"
        "sources:\n"
        + sources_block
    )


_SOURCE_TEMPLATE = """  - id: {id}
    title: {title}
    xml_url: {xml_url}
    category_id: 1
    fetch_group: {fetch_group}
    schedule_class: {schedule_class}
    sanitization_profile: default_html_article
    enabled: {enabled}
"""


def _source_block(**overrides) -> str:
    values = {
        "id": 101,
        "title": "Test Feed",
        "xml_url": "https://example.com/rss",
        "fetch_group": 1,
        "schedule_class": "daily",
        "enabled": "true",
    }
    values.update(overrides)
    return _SOURCE_TEMPLATE.format(**values)


def _fetch_one(db_path: pathlib.Path, sql: str, params=()):
    conn = get_connection(db_path)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _fetch_all(db_path: pathlib.Path, sql: str, params=()):
    conn = get_connection(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _count(db_path: pathlib.Path, table: str) -> int:
    return _fetch_one(db_path, f"SELECT COUNT(*) AS c FROM {table}")["c"]


def _seed_source_state(db_path: pathlib.Path, source_id: int, **overrides) -> None:
    """Inserts a source_state row directly, with explicit defaults for every column."""
    state = {
        "last_fetch_at": None,
        "last_success_at": None,
        "last_http_status": None,
        "etag": None,
        "last_modified": None,
        "consecutive_failures": 0,
        "last_error_class": None,
        "last_error_at": None,
        "health_status": "healthy",
        "quarantine_until": None,
        "updated_at": "2026-06-01T00:00:00Z",
    }
    state.update(overrides)
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO source_state (
                source_id, last_fetch_at, last_success_at, last_http_status,
                etag, last_modified, consecutive_failures, last_error_class,
                last_error_at, health_status, quarantine_until, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                state["last_fetch_at"],
                state["last_success_at"],
                state["last_http_status"],
                state["etag"],
                state["last_modified"],
                state["consecutive_failures"],
                state["last_error_class"],
                state["last_error_at"],
                state["health_status"],
                state["quarantine_until"],
                state["updated_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_result_200_empty_feed() -> FetchResult:
    """A successful 200 response carrying a structurally valid, zero-item feed."""
    return FetchResult(
        status_code=200,
        content=feed_samples.RSS_EMPTY_CHANNEL.encode("utf-8"),
        etag=None,
        last_modified=None,
        error_class=None,
        error_detail=None,
        retry_count=0,
    )


class TestOrchestratorOperations(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name)
        self.db_path = self.config_dir / "test.db"

        with open(self.config_dir / "categories.yaml", "w", encoding="utf-8") as f:
            f.write("""
schema_version: 1
categories:
  1:
    name: Test Category
    slug: test-cat
    enabled: true
""")
        with open(self.config_dir / "retention_policy.yaml", "w", encoding="utf-8") as f:
            f.write("""
schema_version: 1
raw_retention:
  default_days: 14
  delete_batch_size: 500
  dry_run: false
  audit_log: true
""")
        self._write_sources(_source_block())

        self.migrations_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"
        run_migrations(self.db_path, self.migrations_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_sources(self, sources_block: str) -> None:
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(_sources_yaml(sources_block))

    def _load_config(self):
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(errors, [], f"Unexpected config errors: {errors}")
        return config

    def _run(self, config, **kwargs):
        """Runs orchestrate_run with the orchestrator clock pinned to FIXED_NOW."""
        kwargs.setdefault("trigger_type", "manual")
        with patch(
            "modules.ingest.src.orchestrator.get_utc_now_iso8601",
            return_value=FIXED_NOW,
        ):
            return asyncio.run(orchestrate_run(config=config, db_path=self.db_path, **kwargs))

    # --- Contract 1: 304 cache hit ---

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_304_cache_hit_preserves_validators_and_resets_health(self, mock_fetch) -> None:
        _seed_source_state(
            self.db_path,
            101,
            last_fetch_at="2026-05-31T00:00:00Z",
            # 30 days before FIXED_NOW: the source is due without force.
            last_success_at="2026-06-01T00:00:00Z",
            last_http_status=200,
            etag="etag-seed",
            last_modified="Wed, 01 Jun 2026 00:00:00 GMT",
            consecutive_failures=2,
            last_error_class="network_error",
            last_error_at="2026-05-31T00:00:00Z",
            health_status="degraded",
        )
        mock_fetch.return_value = FetchResult(
            status_code=304,
            content=None,
            etag="etag-seed",
            last_modified="Wed, 01 Jun 2026 00:00:00 GMT",
            error_class=None,
            error_detail=None,
            retry_count=0,
        )
        config = self._load_config()

        summary = self._run(config)

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.attempted_source_count, 1)
        self.assertEqual(summary.succeeded_source_count, 1)
        self.assertEqual(summary.new_item_count, 0)

        # The conditional request must carry the seeded validators.
        mock_fetch.assert_awaited_once()
        self.assertEqual(mock_fetch.call_args.kwargs["etag"], "etag-seed")
        self.assertEqual(
            mock_fetch.call_args.kwargs["last_modified"],
            "Wed, 01 Jun 2026 00:00:00 GMT",
        )

        state = _fetch_one(self.db_path, "SELECT * FROM source_state WHERE source_id = 101")
        self.assertEqual(state["etag"], "etag-seed")
        self.assertEqual(state["last_modified"], "Wed, 01 Jun 2026 00:00:00 GMT")
        self.assertEqual(state["last_http_status"], 304)
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertEqual(state["health_status"], "healthy")
        self.assertIsNone(state["last_error_class"])
        self.assertIsNone(state["last_error_at"])
        self.assertIsNone(state["quarantine_until"])
        # A 304 counts as a successful fetch against the pinned clock.
        self.assertEqual(state["last_fetch_at"], FIXED_NOW)
        self.assertEqual(state["last_success_at"], FIXED_NOW)

        attempt = _fetch_one(
            self.db_path,
            "SELECT * FROM fetch_attempt WHERE fetch_run_id = ? AND source_id = 101",
            (summary.fetch_run_id,),
        )
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt["outcome"], "success")
        self.assertEqual(attempt["http_status"], 304)
        self.assertIsNone(attempt["error_class"])
        self.assertIsNone(attempt["error_detail"])
        self.assertEqual(attempt["retry_count"], 0)
        for column in (
            "new_item_count",
            "dedup_matched_count",
            "low_context_count",
            "sanitization_failure_count",
            "normalization_failure_count",
        ):
            self.assertEqual(attempt[column], 0, f"{column} should be 0 on a 304")

        self.assertEqual(_count(self.db_path, "source_item"), 0)

    # --- Contract 2: scope filtering ---

    def _write_three_group_config(self) -> None:
        self._write_sources(
            _source_block(id=101, title="Feed 101", xml_url="https://example.com/rss-101", fetch_group=1)
            + _source_block(id=102, title="Feed 102", xml_url="https://example.com/rss-102", fetch_group=2)
            + _source_block(id=103, title="Feed 103", xml_url="https://example.com/rss-103", fetch_group=3)
        )

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_groups_scope_attempts_only_matching_sources(self, mock_fetch) -> None:
        self._write_three_group_config()
        mock_fetch.side_effect = lambda **kwargs: _fetch_result_200_empty_feed()
        config = self._load_config()

        summary = self._run(config, groups=[1, 3])

        self.assertEqual(summary.run_scope, "groups:[1, 3]")
        self.assertEqual(summary.due_source_count, 2)
        self.assertEqual(summary.attempted_source_count, 2)
        self.assertEqual(summary.succeeded_source_count, 2)

        attempted_ids = {
            row["source_id"]
            for row in _fetch_all(
                self.db_path,
                "SELECT source_id FROM fetch_attempt WHERE fetch_run_id = ?",
                (summary.fetch_run_id,),
            )
        }
        self.assertEqual(attempted_ids, {101, 103})
        self.assertEqual(mock_fetch.await_count, 2)
        fetched_urls = {call.kwargs["xml_url"] for call in mock_fetch.call_args_list}
        self.assertEqual(fetched_urls, {"https://example.com/rss-101", "https://example.com/rss-103"})

        run_row = _fetch_one(
            self.db_path,
            "SELECT run_scope, due_source_count FROM fetch_run WHERE fetch_run_id = ?",
            (summary.fetch_run_id,),
        )
        self.assertEqual(run_row["run_scope"], "groups:[1, 3]")
        self.assertEqual(run_row["due_source_count"], 2)

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_source_ids_scope_attempts_only_listed_sources(self, mock_fetch) -> None:
        self._write_three_group_config()
        mock_fetch.side_effect = lambda **kwargs: _fetch_result_200_empty_feed()
        config = self._load_config()

        summary = self._run(config, source_ids=[102])

        self.assertEqual(summary.run_scope, "sources:[102]")
        self.assertEqual(summary.due_source_count, 1)
        self.assertEqual(summary.attempted_source_count, 1)
        self.assertEqual(summary.succeeded_source_count, 1)

        attempted_ids = {
            row["source_id"]
            for row in _fetch_all(
                self.db_path,
                "SELECT source_id FROM fetch_attempt WHERE fetch_run_id = ?",
                (summary.fetch_run_id,),
            )
        }
        self.assertEqual(attempted_ids, {102})
        mock_fetch.assert_awaited_once()
        self.assertEqual(mock_fetch.call_args.kwargs["xml_url"], "https://example.com/rss-102")

        run_row = _fetch_one(
            self.db_path,
            "SELECT run_scope, due_source_count FROM fetch_run WHERE fetch_run_id = ?",
            (summary.fetch_run_id,),
        )
        self.assertEqual(run_row["run_scope"], "sources:[102]")
        self.assertEqual(run_row["due_source_count"], 1)

    # --- Contract 3: skip paths ---

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_not_due_source_skipped_without_http_call(self, mock_fetch) -> None:
        _seed_source_state(
            self.db_path,
            101,
            # 30 minutes before FIXED_NOW against a 1440-minute interval: not due.
            last_success_at="2026-06-30T23:30:00Z",
            last_http_status=200,
        )
        config = self._load_config()

        summary = self._run(config)

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.skipped_reasons, {"not_due": 1})
        self.assertEqual(summary.attempted_source_count, 0)
        self.assertEqual(summary.succeeded_source_count, 0)
        self.assertEqual(summary.failed_source_count, 0)
        # due_source_count counts in-scope sources before skip checks.
        self.assertEqual(summary.due_source_count, 1)
        mock_fetch.assert_not_awaited()
        self.assertEqual(_count(self.db_path, "fetch_attempt"), 0)

        # The fetch_run row still exists; the skipped source's state is untouched.
        run_row = _fetch_one(
            self.db_path,
            "SELECT run_status, due_source_count FROM fetch_run WHERE fetch_run_id = ?",
            (summary.fetch_run_id,),
        )
        self.assertEqual(run_row["run_status"], "success")
        self.assertEqual(run_row["due_source_count"], 1)
        state = _fetch_one(self.db_path, "SELECT * FROM source_state WHERE source_id = 101")
        self.assertEqual(state["last_success_at"], "2026-06-30T23:30:00Z")
        self.assertIsNone(state["last_fetch_at"])

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_quarantined_source_skipped_without_http_call(self, mock_fetch) -> None:
        _seed_source_state(
            self.db_path,
            101,
            # Old enough to be due, so the quarantine check alone decides the skip.
            last_success_at="2026-06-01T00:00:00Z",
            consecutive_failures=5,
            health_status="quarantined",
            # One hour after FIXED_NOW: quarantine still active.
            quarantine_until="2026-07-01T01:00:00Z",
        )
        config = self._load_config()

        summary = self._run(config)

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.skipped_reasons, {"quarantined": 1})
        self.assertEqual(summary.attempted_source_count, 0)
        self.assertEqual(summary.succeeded_source_count, 0)
        self.assertEqual(summary.failed_source_count, 0)
        mock_fetch.assert_not_awaited()
        self.assertEqual(_count(self.db_path, "fetch_attempt"), 0)

        state = _fetch_one(self.db_path, "SELECT * FROM source_state WHERE source_id = 101")
        self.assertEqual(state["consecutive_failures"], 5)
        self.assertEqual(state["health_status"], "quarantined")
        self.assertEqual(state["quarantine_until"], "2026-07-01T01:00:00Z")

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_disabled_source_is_never_fetched(self, mock_fetch) -> None:
        self._write_sources(
            _source_block(id=101, title="Disabled Feed", xml_url="https://example.com/rss-off", enabled="false")
            + _source_block(id=102, title="Enabled Feed", xml_url="https://example.com/rss-on")
        )
        mock_fetch.side_effect = lambda **kwargs: _fetch_result_200_empty_feed()
        config = self._load_config()

        summary = self._run(config)

        # Disabled sources are excluded from the run scope before execution:
        # they never reach orchestrate_source, so no "disabled" skip reason
        # appears in the summary and due_source_count excludes them.
        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.due_source_count, 1)
        self.assertEqual(summary.attempted_source_count, 1)
        self.assertEqual(summary.succeeded_source_count, 1)
        self.assertEqual(summary.skipped_reasons, {})
        mock_fetch.assert_awaited_once()
        self.assertEqual(mock_fetch.call_args.kwargs["xml_url"], "https://example.com/rss-on")
        attempted_ids = {
            row["source_id"]
            for row in _fetch_all(self.db_path, "SELECT source_id FROM fetch_attempt")
        }
        self.assertEqual(attempted_ids, {102})

        # orchestrate_source still pins the "disabled" skip reason when it is
        # called directly with a disabled source (defense in depth).
        disabled_source = next(s for s in config.sources if s.id == 101)
        result = asyncio.run(
            orchestrate_source(
                source=disabled_source,
                config=config,
                db_path=self.db_path,
                fetch_run_id=summary.fetch_run_id,
                now_str=FIXED_NOW,
                force=False,
                dry_run=False,
                semaphore=asyncio.Semaphore(1),
            )
        )
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.skip_reason, "disabled")
        mock_fetch.assert_awaited_once()  # the direct call made no HTTP request
        self.assertEqual(
            _fetch_one(
                self.db_path,
                "SELECT COUNT(*) AS c FROM fetch_attempt WHERE source_id = 101",
            )["c"],
            0,
        )

    # --- Contract 4: force=True bypass ---

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_force_bypasses_not_due_and_quarantine(self, mock_fetch) -> None:
        self._write_sources(
            _source_block(id=101, title="Not Due Feed", xml_url="https://example.com/rss-101")
            + _source_block(id=102, title="Quarantined Feed", xml_url="https://example.com/rss-102")
        )
        _seed_source_state(
            self.db_path,
            101,
            last_success_at="2026-06-30T23:30:00Z",  # not due
            last_http_status=200,
        )
        _seed_source_state(
            self.db_path,
            102,
            last_success_at="2026-06-01T00:00:00Z",
            consecutive_failures=5,
            health_status="quarantined",
            quarantine_until="2026-07-01T01:00:00Z",  # still active
        )
        mock_fetch.side_effect = lambda **kwargs: _fetch_result_200_empty_feed()
        config = self._load_config()

        summary = self._run(config, force=True)

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.skipped_reasons, {})
        self.assertEqual(summary.attempted_source_count, 2)
        self.assertEqual(summary.succeeded_source_count, 2)
        self.assertEqual(mock_fetch.await_count, 2)

        not_due_state = _fetch_one(self.db_path, "SELECT * FROM source_state WHERE source_id = 101")
        self.assertEqual(not_due_state["last_success_at"], FIXED_NOW)
        self.assertEqual(not_due_state["last_http_status"], 200)

        quarantined_state = _fetch_one(self.db_path, "SELECT * FROM source_state WHERE source_id = 102")
        self.assertEqual(quarantined_state["health_status"], "healthy")
        self.assertEqual(quarantined_state["consecutive_failures"], 0)
        self.assertIsNone(quarantined_state["quarantine_until"])
        self.assertEqual(quarantined_state["last_http_status"], 200)

    # --- Contract 5: dry_run=True ---

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_dry_run_writes_nothing(self, mock_fetch) -> None:
        config = self._load_config()

        summary = self._run(config, dry_run=True)

        self.assertEqual(summary.fetch_run_id, -1)
        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.skipped_reasons, {"dry_run": 1})
        self.assertEqual(summary.attempted_source_count, 0)
        mock_fetch.assert_not_awaited()

        for table in ("fetch_run", "fetch_attempt", "source_state", "source_item"):
            self.assertEqual(_count(self.db_path, table), 0, f"{table} must stay empty on dry-run")

    # --- Contract 6: consecutive-failure quarantine cycle ---

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_consecutive_failures_drive_quarantine_cycle(self, mock_fetch) -> None:
        mock_fetch.return_value = FetchResult(
            status_code=None,
            content=None,
            etag=None,
            last_modified=None,
            error_class="network_error",
            error_detail="mock network failure",
            retry_count=2,
        )
        config = self._load_config()

        expected_progression = [
            (1, "healthy"),
            (2, "healthy"),
            (3, "degraded"),
            (4, "degraded"),
            (5, "quarantined"),
        ]
        for failures, health in expected_progression:
            with self.subTest(failures=failures):
                summary = self._run(config)
                self.assertEqual(summary.run_status, "partial_failure")
                self.assertEqual(summary.attempted_source_count, 1)
                self.assertEqual(summary.failed_source_count, 1)

                state = _fetch_one(
                    self.db_path, "SELECT * FROM source_state WHERE source_id = 101"
                )
                self.assertEqual(state["consecutive_failures"], failures)
                self.assertEqual(state["health_status"], health)
                self.assertEqual(state["last_error_class"], "network_error")
                # The source never succeeded, so it stays due for the next run.
                self.assertIsNone(state["last_success_at"])
                if health == "quarantined":
                    # 24 hours after the pinned clock.
                    self.assertEqual(state["quarantine_until"], "2026-07-02T00:00:00Z")
                    self.assertEqual(summary.quarantined_count, 1)
                else:
                    self.assertIsNone(state["quarantine_until"])
                    self.assertEqual(summary.quarantined_count, 0)

        self.assertEqual(mock_fetch.await_count, 5)
        self.assertEqual(_count(self.db_path, "fetch_attempt"), 5)
        attempts = _fetch_all(
            self.db_path,
            "SELECT outcome, error_class, retry_count FROM fetch_attempt ORDER BY fetch_attempt_id",
        )
        for attempt in attempts:
            self.assertEqual(attempt["outcome"], "failed")
            self.assertEqual(attempt["error_class"], "network_error")
            self.assertEqual(attempt["retry_count"], 2)

        # The next non-force run must not issue an HTTP request for it.
        summary = self._run(config)
        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.skipped_reasons, {"quarantined": 1})
        self.assertEqual(summary.attempted_source_count, 0)
        self.assertEqual(mock_fetch.await_count, 5)
        self.assertEqual(_count(self.db_path, "fetch_attempt"), 5)

    # --- Contract 7: bozo payload -> parse_error ---

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_bozo_payload_maps_to_source_level_parse_error(self, mock_fetch) -> None:
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_samples.MALFORMED_NOT_XML.encode("utf-8"),
            etag="etag-ignored",
            last_modified=None,
            error_class=None,
            error_detail=None,
            retry_count=0,
        )
        config = self._load_config()

        summary = self._run(config)

        self.assertEqual(summary.run_status, "partial_failure")
        self.assertEqual(summary.attempted_source_count, 1)
        self.assertEqual(summary.succeeded_source_count, 0)
        self.assertEqual(summary.failed_source_count, 1)
        self.assertIn("parse_error", summary.error_summary)

        attempt = _fetch_one(
            self.db_path,
            "SELECT * FROM fetch_attempt WHERE fetch_run_id = ? AND source_id = 101",
            (summary.fetch_run_id,),
        )
        self.assertEqual(attempt["outcome"], "failed")
        self.assertEqual(attempt["error_class"], "parse_error")
        self.assertEqual(attempt["http_status"], 200)
        self.assertTrue(attempt["error_detail"].startswith("ParseError:"))
        for column in (
            "new_item_count",
            "dedup_matched_count",
            "low_context_count",
            "sanitization_failure_count",
            "normalization_failure_count",
        ):
            self.assertEqual(attempt[column], 0, f"{column} should be 0 on a parse failure")

        state = _fetch_one(self.db_path, "SELECT * FROM source_state WHERE source_id = 101")
        self.assertEqual(state["last_error_class"], "parse_error")
        self.assertEqual(state["last_http_status"], 200)
        self.assertEqual(state["consecutive_failures"], 1)
        self.assertEqual(state["health_status"], "healthy")
        # A failed parse stores no validators and no success timestamp.
        self.assertIsNone(state["etag"])
        self.assertIsNone(state["last_modified"])
        self.assertIsNone(state["last_success_at"])

        self.assertEqual(_count(self.db_path, "source_item"), 0)


if __name__ == "__main__":
    unittest.main()
