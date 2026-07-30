"""Integration test for the primary end-to-end success flow.

Moved from the former test_integration.py during the Phase 6 test-structure
split. Test logic and assertions are unchanged; the shared feed sample and
config helpers only replace the formerly inlined copies.
"""

import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from modules.ingest.src.config import validate_and_load_config
from modules.ingest.src.database import get_connection, run_migrations
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src.orchestrator import orchestrate_run
from modules.ingest.tests import feed_samples, integration_helpers


class TestSuccessFlow(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name)
        self.db_path = self.config_dir / "test.db"

        integration_helpers.write_base_config(self.config_dir)
        integration_helpers.write_sources(
            self.config_dir, integration_helpers.source_block(title="Test Feed Source")
        )

        self.migrations_dir = integration_helpers.MIGRATIONS_DIR

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_end_to_end_flow(self, mock_fetch) -> None:
        # 1. Setup mock response
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
            etag="etag-123",
            last_modified="Tue, 02 Jun 2026 12:00:00 GMT",
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        # 2. Run migrations
        run_migrations(self.db_path, self.migrations_dir)

        # Verify tables exist
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r["name"] for r in cursor.fetchall()]
            self.assertIn("source_state", tables)
            self.assertIn("source_item", tables)
            self.assertIn("source_item_text", tables)
            self.assertIn("source_item_raw", tables)
            self.assertIn("fetch_run", tables)
            self.assertIn("fetch_attempt", tables)
            self.assertIn("ingest_dedup_marker", tables)
        finally:
            conn.close()

        # 3. Load config
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        # 4. Orchestrate first run
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=self.db_path,
            trigger_type="manual",
            force=True
        ))

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 2)
        self.assertEqual(summary.dedup_matched_count, 0)

        # Verify persisted data
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()

            # Check source_item
            cursor.execute("SELECT * FROM source_item")
            items = cursor.fetchall()
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["source_id"], 101)
            self.assertEqual(items[0]["title"], "Article 1")
            self.assertEqual(items[0]["source_item_guid"], "guid-art1")
            self.assertEqual(items[1]["title"], "Article 2")

            # Check source_item_text
            cursor.execute("SELECT * FROM source_item_text ORDER BY source_item_id ASC")
            texts = cursor.fetchall()
            self.assertEqual(len(texts), 2)
            # Article 1 text should not be low-context
            self.assertEqual(texts[0]["text_processing_status"], "completed")
            self.assertEqual(texts[0]["sanitized_text"], "This is a sufficiently long description that should pass the minimum length check for the test. It contains more than one hundred characters of text in total.")
            # Article 2 text should be low-context (too_short)
            self.assertEqual(texts[1]["text_processing_status"], "low_context")
            self.assertEqual(texts[1]["text_processing_reason"], "too_short")

            # Check source_item_raw
            cursor.execute("SELECT * FROM source_item_raw")
            raws = cursor.fetchall()
            self.assertEqual(len(raws), 2)
            self.assertIn("sufficiently long description", raws[0]["raw_payload"])

            # Check source_state
            cursor.execute("SELECT * FROM source_state WHERE source_id = 101")
            state = cursor.fetchone()
            self.assertEqual(state["health_status"], "healthy")
            self.assertEqual(state["etag"], "etag-123")
            self.assertEqual(state["last_modified"], "Tue, 02 Jun 2026 12:00:00 GMT")

            # Check dedup markers
            cursor.execute("SELECT * FROM ingest_dedup_marker")
            markers = cursor.fetchall()
            self.assertEqual(len(markers), 2)

        finally:
            conn.close()

        # 5. Orchestrate second run (deduplication check)
        summary2 = asyncio.run(orchestrate_run(
            config=config,
            db_path=self.db_path,
            trigger_type="manual",
            force=True
        ))

        self.assertEqual(summary2.run_status, "success")
        self.assertEqual(summary2.new_item_count, 0)
        self.assertEqual(summary2.dedup_matched_count, 2)


if __name__ == "__main__":
    unittest.main()
