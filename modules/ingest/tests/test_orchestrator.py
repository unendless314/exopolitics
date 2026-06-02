import unittest
import tempfile
import pathlib
import sqlite3
import asyncio
from unittest.mock import patch, MagicMock

from modules.ingest.src.config import IngestConfig, SourceConfig, CategoryConfig, ScheduleClassConfig
from modules.ingest.src.database import get_connection, run_migrations
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src.orchestrator import orchestrate_run

class TestOrchestrator(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Create a real temporary database file to avoid SQLite :memory: multi-connection isolation issues
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_orchestration.db"
        self.migrations_dir = pathlib.Path(__file__).parent.parent / "src" / "migrations"
        
        # Apply initial migrations
        run_migrations(self.db_path, self.migrations_dir)

        # Standard config
        self.categories = {
            1: CategoryConfig(id=1, name="Disclosure", slug="disclosure", enabled=True),
        }
        self.schedule_classes = {
            "daily": ScheduleClassConfig(name="daily", target_interval_minutes=1440, description="Daily"),
            "hourly": ScheduleClassConfig(name="hourly", target_interval_minutes=60, description="Hourly")
        }
        
        self.source1 = SourceConfig(
            id=101,
            title="AARO Feed",
            xml_url="https://example.com/aaro.xml",
            category_id=1,
            fetch_group=1,
            schedule_class="daily",
            enabled=True
        )
        self.source2 = SourceConfig(
            id=102,
            title="Nuforc Feed",
            xml_url="https://example.com/nuforc.xml",
            category_id=1,
            fetch_group=1,
            schedule_class="hourly",
            enabled=True
        )

        self.config = IngestConfig(
            categories=self.categories,
            schedule_classes=self.schedule_classes,
            sources=[self.source1, self.source2]
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    async def test_run_success_200_with_items(self, mock_fetch) -> None:
        # Source 101 returns a valid feed with 2 entries
        xml_content = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>UFO Over Pacific</title>
      <link>https://example.com/pacific</link>
      <guid>guid-pacific</guid>
    </item>
    <item>
      <title>UFO Over Atlantic</title>
      <link>https://example.com/atlantic</link>
      <guid>guid-atlantic</guid>
    </item>
  </channel>
</rss>
"""
        # Mock FetchResult
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=xml_content,
            etag="etag-123",
            last_modified="Wed, 21 Oct 2015 07:28:00 GMT",
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        # Run only source1
        summary = await orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            trigger_type="scheduled",
            source_ids=[101]
        )

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.attempted_source_count, 1)
        self.assertEqual(summary.succeeded_source_count, 1)
        self.assertEqual(summary.failed_source_count, 0)
        self.assertEqual(summary.new_item_count, 2)
        self.assertEqual(summary.dedup_matched_count, 0)

        # Connect and check database state
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            
            # Check source_item
            cursor.execute("SELECT * FROM source_item WHERE source_id = 101")
            items = cursor.fetchall()
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["source_item_guid"], "guid-pacific")
            self.assertEqual(items[1]["source_item_guid"], "guid-atlantic")

            # Check source_state
            cursor.execute("SELECT * FROM source_state WHERE source_id = 101")
            state = cursor.fetchone()
            self.assertIsNotNone(state)
            self.assertEqual(state["health_status"], "healthy")
            self.assertEqual(state["etag"], "etag-123")
            self.assertEqual(state["consecutive_failures"], 0)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    async def test_run_success_304_cache_hit(self, mock_fetch) -> None:
        # Pre-seed source_state with etag
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                INSERT INTO source_state (source_id, etag, health_status, consecutive_failures, updated_at) 
                VALUES (101, 'old-etag-123', 'healthy', 0, '2026-06-02T12:00:00Z')
            """)
            conn.commit()
        finally:
            conn.close()

        # Mock 304 response
        mock_fetch.return_value = FetchResult(
            status_code=304,
            content=None,
            etag="old-etag-123",
            last_modified=None,
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        summary = await orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            trigger_type="scheduled",
            source_ids=[101]
        )

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 0)
        self.assertEqual(summary.dedup_matched_count, 0)

        # Check DB to confirm etag is retained
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM source_state WHERE source_id = 101")
            state = cursor.fetchone()
            self.assertEqual(state["etag"], "old-etag-123")
            self.assertEqual(state["last_http_status"], 304)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    async def test_run_partial_failure_isolation(self, mock_fetch) -> None:
        # source1 (101) succeeds, source2 (102) fails with 404
        xml_content = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <item><title>Item 1</title><link>https://example.com/1</link></item>
  </channel>
</rss>
"""
        def fetch_side_effect(xml_url, etag=None, last_modified=None, semaphore=None):
            if "aaro.xml" in xml_url:
                return FetchResult(200, xml_content, None, None, None, None, 0)
            else:
                return FetchResult(404, None, None, None, "http_error_4xx", "HTTP 404: Not Found", 0)

        mock_fetch.side_effect = fetch_side_effect

        summary = await orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            trigger_type="scheduled"
        )

        # One success, one failure -> partial_failure
        self.assertEqual(summary.run_status, "partial_failure")
        self.assertEqual(summary.attempted_source_count, 2)
        self.assertEqual(summary.succeeded_source_count, 1)
        self.assertEqual(summary.failed_source_count, 1)
        self.assertEqual(summary.new_item_count, 1)

        # Verify failed source health status is updated
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM source_state WHERE source_id = 102")
            state = cursor.fetchone()
            self.assertIsNotNone(state)
            self.assertEqual(state["consecutive_failures"], 1)
            self.assertEqual(state["last_error_class"], "http_error_4xx")
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    async def test_force_override_bypasses_due_checks(self, mock_fetch) -> None:
        # Pre-seed source as non-due (daily fetched 1 hour ago)
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                INSERT INTO source_state (source_id, last_success_at, health_status, consecutive_failures, updated_at) 
                VALUES (101, '2026-06-02T11:00:00Z', 'healthy', 0, '2026-06-02T11:00:00Z')
            """)
            conn.commit()
        finally:
            conn.close()

        mock_fetch.return_value = FetchResult(304, None, None, None, None, None, 0)

        # 1. Scheduled run without force -> skipped (not_due)
        summary_normal = await orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            trigger_type="scheduled",
            source_ids=[101],
            force=False
        )
        self.assertEqual(summary_normal.skipped_reasons.get("not_due"), 1)
        self.assertEqual(summary_normal.attempted_source_count, 0)

        # 2. Run with force -> executed!
        summary_force = await orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            trigger_type="manual",
            source_ids=[101],
            force=True
        )
        self.assertEqual(summary_force.skipped_reasons.get("not_due", 0), 0)
        self.assertEqual(summary_force.attempted_source_count, 1)

    async def test_dry_run_semantics(self) -> None:
        # In dry run, it should list/resolve due sources, but not make calls or database modifications
        summary = await orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            trigger_type="manual",
            dry_run=True
        )
        
        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.attempted_source_count, 0)  # dry-run has zero actual attempts
        self.assertEqual(summary.skipped_reasons.get("dry_run"), 2)  # both sources planned/skipped as dry_run
        
        # Verify no database state records were modified/created
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM source_state")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT COUNT(*) FROM fetch_run")
            self.assertEqual(cursor.fetchone()[0], 0)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    async def test_run_level_critical_failure_marks_failed(self, mock_fetch) -> None:
        # Mock fetch_feed to return unexpected_error, which should bubble up and fail the whole run
        mock_fetch.return_value = FetchResult(
            status_code=None,
            content=None,
            etag=None,
            last_modified=None,
            error_class="unexpected_error",
            error_detail="Disk full or database locked",
            retry_count=0
        )

        summary = await orchestrate_run(
            config=self.config,
            db_path=self.db_path,
            trigger_type="manual",
            source_ids=[101]
        )

        self.assertEqual(summary.run_status, "failed")
        self.assertEqual(summary.failed_source_count, 1)
        self.assertIn("Unexpected run-level error during fetch", summary.error_summary)

if __name__ == "__main__":
    unittest.main()
