import unittest
from modules.ingest.src.dedup import generate_dedup_keys, MIN_TITLE_HASH_LENGTH

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

if __name__ == "__main__":
    unittest.main()
