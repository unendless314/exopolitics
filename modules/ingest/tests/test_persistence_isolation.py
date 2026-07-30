"""Integration tests for item-level persistence isolation.

Moved from the former test_integration.py during the Phase 6 test-structure
split: the item savepoint rollback contract and the non-dedup IntegrityError
classification contract. Test logic and assertions are unchanged; the shared
feed sample and config helpers only replace the formerly inlined copies.
"""

import asyncio
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from modules.ingest.src.config import validate_and_load_config
from modules.ingest.src.database import (
    SourceItemRawRepository,
    SourceItemTextRepository,
    get_connection,
    run_migrations,
)
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src.orchestrator import orchestrate_run
from modules.ingest.tests import feed_samples, integration_helpers


class TestPersistenceIsolation(unittest.TestCase):
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
    def test_item_savepoint_rollback(self, mock_fetch) -> None:
        """
        Verify that if insertion of source_item_text fails (e.g., database constraint
        or write error), the savepoint is rolled back and the source_item is NOT committed.
        """
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
            etag="etag-123",
            last_modified="Tue, 02 Jun 2026 12:00:00 GMT",
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)

        # Mock SourceItemTextRepository.insert to raise an exception for Article 2 only
        original_insert = SourceItemTextRepository.insert
        def mock_insert_text(self_repo, text_data):
            # We look at the target file or mock a failure specifically for the second item
            # Let's read the source_item title to identify it
            cursor = self_repo.conn.cursor()
            cursor.execute("SELECT title FROM source_item WHERE source_item_id = ?", (text_data["source_item_id"],))
            title = cursor.fetchone()["title"]
            if "Article 2" in title:
                raise sqlite3.Error("Mocked database insert failure for text")
            return original_insert(self_repo, text_data)

        with patch.object(SourceItemTextRepository, "insert", mock_insert_text):
            summary = asyncio.run(orchestrate_run(
                config=config,
                db_path=self.db_path,
                trigger_type="manual",
                force=True
            ))

        # The run should still succeed at source-level because of failure isolation
        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 1) # Only Article 1 succeeded

        # Connect and check that Article 2's source_item and dedup marker DO NOT exist
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM source_item")
            items = cursor.fetchall()
            self.assertEqual(len(items), 1) # Only 1 item exists in DB
            self.assertEqual(items[0]["title"], "Article 1")

            cursor.execute("SELECT * FROM ingest_dedup_marker")
            markers = cursor.fetchall()
            self.assertEqual(len(markers), 1) # Only 1 marker exists
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_non_dedup_integrity_error_counts_as_failure(self, mock_fetch) -> None:
        """
        An IntegrityError that is NOT a dedup key conflict (e.g. CHECK violation)
        must roll back the item, count as a persistence failure, and must NOT
        inflate the dedup count.
        """
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
            etag="etag-123",
            last_modified=None,
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        # Raise a non-dedup IntegrityError at raw-record insert time (outside the
        # sanitizer sub-block), so the item-level integrity handler decides the class
        original_raw_insert = SourceItemRawRepository.insert
        def mock_insert_raw(self_repo, raw_data):
            cursor = self_repo.conn.cursor()
            cursor.execute("SELECT title FROM source_item WHERE source_item_id = ?", (raw_data["source_item_id"],))
            title = cursor.fetchone()["title"]
            if "Article 2" in title:
                raise sqlite3.IntegrityError("CHECK constraint failed: retention_class")
            return original_raw_insert(self_repo, raw_data)

        with patch.object(SourceItemRawRepository, "insert", mock_insert_raw):
            summary = asyncio.run(orchestrate_run(
                config=config, db_path=self.db_path, trigger_type="manual", force=True
            ))

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 1)
        self.assertEqual(summary.dedup_matched_count, 0)

        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            # Article 2 fully rolled back
            cursor.execute("SELECT COUNT(*) AS c FROM source_item")
            self.assertEqual(cursor.fetchone()["c"], 1)
            cursor.execute("SELECT COUNT(*) AS c FROM ingest_dedup_marker")
            self.assertEqual(cursor.fetchone()["c"], 1)
            # Counted as a persistence failure, not a dedup match
            cursor.execute(
                "SELECT normalization_failure_count, dedup_matched_count FROM fetch_attempt ORDER BY fetch_attempt_id DESC LIMIT 1"
            )
            attempt = cursor.fetchone()
            self.assertEqual(attempt["normalization_failure_count"], 1)
            self.assertEqual(attempt["dedup_matched_count"], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
