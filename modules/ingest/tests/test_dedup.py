import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from modules.ingest.src.config import validate_and_load_config
from modules.ingest.src.database import DedupMarkerRepository, get_connection, run_migrations
from modules.ingest.src.dedup import generate_dedup_keys, MIN_TITLE_HASH_LENGTH
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src.orchestrator import orchestrate_run
from modules.ingest.tests import feed_samples, integration_helpers

LONG_TITLE = "A Sufficiently Long Article Title"
SHORT_TITLE = "Short"  # below MIN_TITLE_HASH_LENGTH

class TestDeduplication(unittest.TestCase):
    def test_dedup_rule_precedence_url_first(self) -> None:
        # URL is the strongest identity: global across sources, so it outranks GUID
        key, rule, extras = generate_dedup_keys(
            source_id=42,
            guid="my-unique-guid",
            canonical_url="https://example.com/item",
            title=LONG_TITLE,
            published_at="2026-06-02T12:00:00Z"
        )
        self.assertEqual(rule, "url")
        self.assertEqual(key, "url:https://example.com/item")

    def test_dedup_rule_precedence_guid_second(self) -> None:
        key, rule, extras = generate_dedup_keys(
            source_id=42,
            guid="my-unique-guid",
            canonical_url=None,
            title=LONG_TITLE,
            published_at="2026-06-02T12:00:00Z"
        )
        self.assertEqual(rule, "guid")
        self.assertEqual(key, "guid:42:my-unique-guid")

    def test_dedup_rule_precedence_tp_third(self) -> None:
        key, rule, extras = generate_dedup_keys(
            source_id=42,
            guid="",
            canonical_url=None,
            title=LONG_TITLE,
            published_at="2026-06-02T12:00:00Z"
        )
        self.assertEqual(rule, "tp")
        self.assertTrue(key.startswith("tp:42:"))

    def test_dedup_rule_precedence_fh_fourth(self) -> None:
        key, rule, extras = generate_dedup_keys(
            source_id=42,
            guid=None,
            canonical_url="",
            title=LONG_TITLE,
            published_at=None
        )
        self.assertEqual(rule, "fh")
        self.assertTrue(key.startswith("fh:42:"))

    def test_title_hash_marker_emitted_for_long_titles(self) -> None:
        key, rule, extras = generate_dedup_keys(
            source_id=1,
            guid=None,
            canonical_url="https://example.com/item",
            title=LONG_TITLE,
            published_at=None
        )
        self.assertEqual(len(extras), 1)
        th_key, th_rule = extras[0]
        self.assertEqual(th_rule, "th")
        self.assertTrue(th_key.startswith("th:"))
        self.assertGreater(len(LONG_TITLE), MIN_TITLE_HASH_LENGTH)

    def test_title_hash_marker_skipped_for_short_titles(self) -> None:
        self.assertLess(len(SHORT_TITLE), MIN_TITLE_HASH_LENGTH)
        key, rule, extras = generate_dedup_keys(
            source_id=1,
            guid=None,
            canonical_url="https://example.com/item",
            title=SHORT_TITLE,
            published_at=None
        )
        self.assertEqual(extras, [])

    def test_cross_source_dedup_rules(self) -> None:
        # URL is cross-source (global)
        key1_url, _, _ = generate_dedup_keys(1, None, "https://example.com/same", SHORT_TITLE, None)
        key2_url, _, _ = generate_dedup_keys(2, None, "https://example.com/same", "Other", None)
        self.assertEqual(key1_url, key2_url)
        self.assertEqual(key1_url, "url:https://example.com/same")

        # GUID is source-scoped
        key1_guid, _, _ = generate_dedup_keys(1, "same-guid", None, SHORT_TITLE, None)
        key2_guid, _, _ = generate_dedup_keys(2, "same-guid", None, SHORT_TITLE, None)
        self.assertNotEqual(key1_guid, key2_guid)
        self.assertEqual(key1_guid, "guid:1:same-guid")
        self.assertEqual(key2_guid, "guid:2:same-guid")

        # TP is source-scoped
        key1_tp, _, _ = generate_dedup_keys(1, None, None, LONG_TITLE, "2026-06-02T12:00:00Z")
        key2_tp, _, _ = generate_dedup_keys(2, None, None, LONG_TITLE, "2026-06-02T12:00:00Z")
        self.assertNotEqual(key1_tp, key2_tp)

    def test_title_hash_is_cross_source_and_url_independent(self) -> None:
        # Same article syndicated across sources: different URLs and GUIDs,
        # but the normalized title hash marker must match.
        _, _, extras1 = generate_dedup_keys(
            1, "guid-a", "https://news.google.com/rss/articles/AAA", LONG_TITLE, "2026-06-02T12:00:00Z"
        )
        _, _, extras2 = generate_dedup_keys(
            2, "guid-b", "https://news.google.com/rss/articles/BBB", LONG_TITLE, "2026-06-03T08:00:00Z"
        )
        self.assertEqual(extras1, extras2)

    def test_title_hash_normalizes_case_and_whitespace(self) -> None:
        _, _, extras1 = generate_dedup_keys(1, None, "https://a.example.com/x", "  The   Same   TITLE Here  ", None)
        _, _, extras2 = generate_dedup_keys(2, None, "https://b.example.com/y", "the same title here", None)
        self.assertEqual(extras1, extras2)
        self.assertEqual(len(extras1), 1)


class TestDedupIntegration(unittest.TestCase):
    """Pipeline-level dedup contracts (cross-source title hash, dedup race,
    dedup audit logging). Moved from the former test_integration.py during the
    Phase 6 test-structure split; test logic and assertions are unchanged.
    """

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
