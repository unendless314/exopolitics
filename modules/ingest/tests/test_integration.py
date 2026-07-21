import unittest
import pathlib
import tempfile
import sqlite3
import asyncio
from unittest.mock import patch


from modules.ingest.src.config import validate_and_load_config
from modules.ingest.src.database import (
    run_migrations,
    get_connection,
    SourceItemTextRepository,
    SourceItemRawRepository,
    DedupMarkerRepository,
)
from modules.ingest.src.orchestrator import orchestrate_run, IngestRunSummary
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src import sanitizer

# Mock RSS Feed XML content
MOCK_FEED_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Mock Feed</title>
    <link>https://example.com/</link>
    <description>Mock Description</description>
    <item>
      <title>Article 1</title>
      <link>https://example.com/art1</link>
      <guid>guid-art1</guid>
      <pubDate>Tue, 02 Jun 2026 12:00:00 GMT</pubDate>
      <description><![CDATA[<p>This is a sufficiently long description that should pass the minimum length check for the test. It contains more than one hundred characters of text in total.</p>]]></description>
    </item>
    <item>
      <title>Article 2</title>
      <link>https://example.com/art2</link>
      <guid>guid-art2</guid>
      <pubDate>Tue, 02 Jun 2026 13:00:00 GMT</pubDate>
      <description><![CDATA[<p>Short body text.</p>]]></description>
    </item>
  </channel>
</rss>
"""

class TestIntegration(unittest.TestCase):
    def setUp(self) -> None:
        # Create temp config directory
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name)

        # Temp DB path
        self.db_path = self.config_dir / "test.db"

        # Write config files
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
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write("""
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
    description: Daily
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
    decode_entities: true
    remove_selectors: [script]
sources:
  - id: 101
    title: Test Feed Source
    xml_url: https://example.com/rss
    category_id: 1
    fetch_group: 1
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
""")

        # Migrations directory
        self.migrations_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_end_to_end_flow(self, mock_fetch) -> None:
        # 1. Setup mock response
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=MOCK_FEED_XML,
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

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_item_savepoint_rollback(self, mock_fetch) -> None:
        """
        Verify that if insertion of source_item_text fails (e.g., database constraint
        or write error), the savepoint is rolled back and the source_item is NOT committed.
        """
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=MOCK_FEED_XML,
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
    def test_sanitization_failure_counted_once_when_fallback_insert_fails(self, mock_fetch) -> None:
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=MOCK_FEED_XML,
            etag="etag-123",
            last_modified="Tue, 02 Jun 2026 12:00:00 GMT",
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)

        def mock_sanitize_item(entry, normalized_title, profile, method_label="bs4_default"):
            if normalized_title == "Article 2":
                raise ValueError("mock sanitization failure")
            return sanitizer.sanitize_item(entry, normalized_title, profile, method_label)

        original_insert = SourceItemTextRepository.insert

        def mock_insert_text(self_repo, text_data):
            cursor = self_repo.conn.cursor()
            cursor.execute("SELECT title FROM source_item WHERE source_item_id = ?", (text_data["source_item_id"],))
            row = cursor.fetchone()
            if row and row["title"] == "Article 2":
                raise sqlite3.Error("Mocked fallback insert failure")
            return original_insert(self_repo, text_data)

        with patch("modules.ingest.src.orchestrator.sanitize_item", side_effect=mock_sanitize_item):
            with patch.object(SourceItemTextRepository, "insert", mock_insert_text):
                summary = asyncio.run(orchestrate_run(
                    config=config,
                    db_path=self.db_path,
                    trigger_type="manual",
                    force=True
                ))

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 1)

        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT sanitization_failure_count, normalization_failure_count FROM fetch_attempt")
            attempt = cursor.fetchone()
            self.assertEqual(attempt["sanitization_failure_count"], 1)
            self.assertEqual(attempt["normalization_failure_count"], 0)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_missing_body_is_not_counted_as_sanitization_failure(self, mock_fetch) -> None:
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=MOCK_FEED_XML,
            etag="etag-123",
            last_modified="Tue, 02 Jun 2026 12:00:00 GMT",
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)

        def mock_sanitize_item(entry, normalized_title, profile, method_label="bs4_default"):
            if normalized_title == "Article 2":
                return {
                    "sanitized_text": "",
                    "html_detected": False,
                    "was_truncated": False,
                    "text_processing_status": "failed",
                    "text_processing_reason": "missing_body",
                    "raw_text_length": 0,
                    "sanitized_text_length": 0,
                    "reduction_ratio": 0.0,
                    "sanitization_method": method_label,
                    "raw_payload": ""
                }
            return sanitizer.sanitize_item(entry, normalized_title, profile, method_label)

        with patch("modules.ingest.src.orchestrator.sanitize_item", side_effect=mock_sanitize_item):
            summary = asyncio.run(orchestrate_run(
                config=config,
                db_path=self.db_path,
                trigger_type="manual",
                force=True
            ))

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 2)

        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT text_processing_status, text_processing_reason FROM source_item_text ORDER BY source_item_id ASC")
            texts = cursor.fetchall()
            self.assertEqual(texts[1]["text_processing_status"], "failed")
            self.assertEqual(texts[1]["text_processing_reason"], "missing_body")

            cursor.execute("SELECT sanitization_failure_count, normalization_failure_count FROM fetch_attempt")
            attempt = cursor.fetchone()
            self.assertEqual(attempt["sanitization_failure_count"], 0)
            self.assertEqual(attempt["normalization_failure_count"], 0)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_sanitization_failure_counted_when_fallback_insert_succeeds(self, mock_fetch) -> None:
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=MOCK_FEED_XML,
            etag="etag-123",
            last_modified="Tue, 02 Jun 2026 12:00:00 GMT",
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)

        def mock_sanitize_item(entry, normalized_title, profile, method_label="bs4_default"):
            if normalized_title == "Article 2":
                raise ValueError("mock sanitization failure")
            return sanitizer.sanitize_item(entry, normalized_title, profile, method_label)

        with patch("modules.ingest.src.orchestrator.sanitize_item", side_effect=mock_sanitize_item):
            summary = asyncio.run(orchestrate_run(
                config=config,
                db_path=self.db_path,
                trigger_type="manual",
                force=True
            ))

        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 2)

        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT text_processing_status, text_processing_reason FROM source_item_text ORDER BY source_item_id ASC")
            texts = cursor.fetchall()
            self.assertEqual(texts[1]["text_processing_status"], "failed")
            self.assertEqual(texts[1]["text_processing_reason"], "sanitizer_exception")

            cursor.execute("SELECT sanitization_failure_count, normalization_failure_count FROM fetch_attempt")
            attempt = cursor.fetchone()
            self.assertEqual(attempt["sanitization_failure_count"], 1)
            self.assertEqual(attempt["normalization_failure_count"], 0)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_cross_source_title_dedup(self, mock_fetch) -> None:
        """
        Two different sources carry the same article (identical title, different
        URLs and GUIDs). The global title-hash marker must dedup the second copy
        even though primary keys differ across sources.
        """
        SHARED_TITLE = "Shared Cross-Source Article Title"
        SOURCE_A_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Feed A</title>
    <item>
      <title>{SHARED_TITLE}</title>
      <link>https://a.example.com/story/1?utm_source=rss</link>
      <guid>guid-a-1</guid>
      <pubDate>Tue, 02 Jun 2026 12:00:00 GMT</pubDate>
      <description><![CDATA[<p>This is a sufficiently long description that should pass the minimum length check for the test. It contains more than one hundred characters of text in total.</p>]]></description>
    </item>
    <item>
      <title>Unique Alpha Article For Source One</title>
      <link>https://a.example.com/story/2</link>
      <guid>guid-a-2</guid>
      <pubDate>Tue, 02 Jun 2026 13:00:00 GMT</pubDate>
      <description><![CDATA[<p>This is a sufficiently long description that should pass the minimum length check for the test. It contains more than one hundred characters of text in total.</p>]]></description>
    </item>
  </channel>
</rss>
""".encode("utf-8")
        SOURCE_B_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Feed B</title>
    <item>
      <title>{SHARED_TITLE}</title>
      <link>https://b.example.com/other/9?fbclid=xyz</link>
      <guid>guid-b-9</guid>
      <pubDate>Tue, 02 Jun 2026 14:00:00 GMT</pubDate>
      <description><![CDATA[<p>This is a sufficiently long description that should pass the minimum length check for the test. It contains more than one hundred characters of text in total.</p>]]></description>
    </item>
    <item>
      <title>Unique Bravo Article For Source Two</title>
      <link>https://b.example.com/other/8</link>
      <guid>guid-b-8</guid>
      <pubDate>Tue, 02 Jun 2026 15:00:00 GMT</pubDate>
      <description><![CDATA[<p>This is a sufficiently long description that should pass the minimum length check for the test. It contains more than one hundred characters of text in total.</p>]]></description>
    </item>
  </channel>
</rss>
""".encode("utf-8")

        def fake_fetch(xml_url, **kwargs):
            content = SOURCE_A_XML if xml_url == "https://example.com/rss-a" else SOURCE_B_XML
            return FetchResult(
                status_code=200,
                content=content,
                etag=None,
                last_modified=None,
                error_class=None,
                error_detail=None,
                retry_count=0
            )
        mock_fetch.side_effect = fake_fetch

        # Rewrite config with two sources
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write("""
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
    description: Daily
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
    decode_entities: true
    remove_selectors: [script]
sources:
  - id: 101
    title: Test Feed A
    xml_url: https://example.com/rss-a
    category_id: 1
    fetch_group: 1
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
  - id: 102
    title: Test Feed B
    xml_url: https://example.com/rss-b
    category_id: 1
    fetch_group: 1
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
""")

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=self.db_path,
            trigger_type="manual",
            force=True
        ))

        # 4 items fetched across 2 sources; the shared title must dedup once
        self.assertEqual(summary.run_status, "success")
        self.assertEqual(summary.new_item_count, 3)
        self.assertEqual(summary.dedup_matched_count, 1)

        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            # Only one copy of the shared article exists, regardless of which source won
            cursor.execute("SELECT COUNT(*) AS c FROM source_item WHERE title = ?", (SHARED_TITLE,))
            self.assertEqual(cursor.fetchone()["c"], 1)

            cursor.execute("SELECT COUNT(*) AS c FROM source_item")
            self.assertEqual(cursor.fetchone()["c"], 3)

            # Each persisted item holds a primary marker plus a title-hash marker
            cursor.execute("SELECT dedup_rule, COUNT(*) AS c FROM ingest_dedup_marker GROUP BY dedup_rule")
            rule_counts = {row["dedup_rule"]: row["c"] for row in cursor.fetchall()}
            self.assertEqual(rule_counts.get("url"), 3)
            self.assertEqual(rule_counts.get("th"), 3)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_dedup_key_conflict_counts_as_dedup_match(self, mock_fetch) -> None:
        """
        Race path: when the pre-check misses but the insert collides on a dedup
        identity key (UNIQUE constraint on source_item.ingest_dedup_key or
        ingest_dedup_marker.dedup_key), the item must count as a dedup match,
        not as a persistence failure.
        """
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=MOCK_FEED_XML,
            etag="etag-123",
            last_modified=None,
            error_class=None,
            error_detail=None,
            retry_count=0
        )

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        # First run persists both items and their markers
        summary1 = asyncio.run(orchestrate_run(
            config=config, db_path=self.db_path, trigger_type="manual", force=True
        ))
        self.assertEqual(summary1.new_item_count, 2)

        # Second run: simulate a pre-check miss while the keys are actually present,
        # forcing a UNIQUE collision on insert (the dedup race path)
        with patch.object(DedupMarkerRepository, "find_match", return_value=None):
            summary2 = asyncio.run(orchestrate_run(
                config=config, db_path=self.db_path, trigger_type="manual", force=True
            ))

        self.assertEqual(summary2.run_status, "success")
        self.assertEqual(summary2.new_item_count, 0)
        self.assertEqual(summary2.dedup_matched_count, 2)

        # The race must not be misreported as a persistence failure
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT normalization_failure_count FROM fetch_attempt ORDER BY fetch_attempt_id DESC LIMIT 1"
            )
            self.assertEqual(cursor.fetchone()["normalization_failure_count"], 0)
            cursor.execute("SELECT COUNT(*) AS c FROM source_item")
            self.assertEqual(cursor.fetchone()["c"], 2)
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
            content=MOCK_FEED_XML,
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

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_title_hash_dedup_hit_is_logged(self, mock_fetch) -> None:
        """
        When a title-hash marker triggers the dedup pre-check, the log record must
        carry the matched rule, matched key, and the skipped item's title so that
        false merges can be audited after a run.
        """
        SHARED_TITLE = "Shared Title For Dedup Logging"
        def feed_xml(link: str) -> bytes:
            return f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Mock Feed</title>
    <item>
      <title>{SHARED_TITLE}</title>
      <link>{link}</link>
      <guid>guid-{link.rsplit('/', 1)[-1]}</guid>
      <pubDate>Tue, 02 Jun 2026 12:00:00 GMT</pubDate>
      <description><![CDATA[<p>This is a sufficiently long description that should pass the minimum length check for the test. It contains more than one hundred characters of text in total.</p>]]></description>
    </item>
  </channel>
</rss>
""".encode("utf-8")

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_xml("https://example.com/article-v1"),
            etag=None, last_modified=None, error_class=None, error_detail=None, retry_count=0
        )
        summary1 = asyncio.run(orchestrate_run(
            config=config, db_path=self.db_path, trigger_type="manual", force=True
        ))
        self.assertEqual(summary1.new_item_count, 1)

        # Same article under a different URL: primary url key misses, th marker hits
        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_xml("https://example.com/article-v2"),
            etag=None, last_modified=None, error_class=None, error_detail=None, retry_count=0
        )
        with self.assertLogs("ingest.orchestrator", level="INFO") as captured:
            summary2 = asyncio.run(orchestrate_run(
                config=config, db_path=self.db_path, trigger_type="manual", force=True
            ))

        self.assertEqual(summary2.new_item_count, 0)
        self.assertEqual(summary2.dedup_matched_count, 1)

        log_text = "\n".join(captured.output)
        self.assertIn("matched_rule=th", log_text)
        self.assertIn("matched_key=th:", log_text)
        self.assertIn(SHARED_TITLE, log_text)
        self.assertIn("source 101", log_text)

if __name__ == "__main__":
    unittest.main()
