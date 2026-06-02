import unittest
from modules.ingest.src.dedup import generate_dedup_key_and_rule

class TestDeduplication(unittest.TestCase):
    def test_dedup_rule_precedence_guid_first(self) -> None:
        # All fields present, should choose GUID first
        key, rule = generate_dedup_key_and_rule(
            source_id=42,
            guid="my-unique-guid",
            canonical_url="https://example.com/item",
            title="Spotted Object",
            published_at="2026-06-02T12:00:00Z"
        )
        self.assertEqual(rule, "guid")
        self.assertEqual(key, "guid:42:my-unique-guid")

    def test_dedup_rule_precedence_url_second(self) -> None:
        # GUID missing, should choose URL
        key, rule = generate_dedup_key_and_rule(
            source_id=42,
            guid=None,
            canonical_url="https://example.com/item",
            title="Spotted Object",
            published_at="2026-06-02T12:00:00Z"
        )
        self.assertEqual(rule, "url")
        self.assertEqual(key, "url:https://example.com/item")

    def test_dedup_rule_precedence_tp_third(self) -> None:
        # GUID and URL missing, should choose TP heuristic
        key, rule = generate_dedup_key_and_rule(
            source_id=42,
            guid="",
            canonical_url=None,
            title="Spotted Object",
            published_at="2026-06-02T12:00:00Z"
        )
        self.assertEqual(rule, "tp")
        self.assertTrue(key.startswith("tp:42:"))

    def test_dedup_rule_precedence_fh_fourth(self) -> None:
        # Only title available, missing published_at, should fall back to FH
        key, rule = generate_dedup_key_and_rule(
            source_id=42,
            guid=None,
            canonical_url="",
            title="Spotted Object Only",
            published_at=None
        )
        self.assertEqual(rule, "fh")
        self.assertTrue(key.startswith("fh:42:"))

    def test_cross_source_dedup_rules(self) -> None:
        # 1. URL is cross-source (global) -> same URL across two sources must yield identical keys
        key1_url, _ = generate_dedup_key_and_rule(1, None, "https://example.com/same", "Title 1", None)
        key2_url, _ = generate_dedup_key_and_rule(2, None, "https://example.com/same", "Title 2", None)
        self.assertEqual(key1_url, key2_url)
        self.assertEqual(key1_url, "url:https://example.com/same")

        # 2. GUID is source-scoped -> same GUID across two sources must yield different keys
        key1_guid, _ = generate_dedup_key_and_rule(1, "same-guid", "https://example.com/1", "Title 1", None)
        key2_guid, _ = generate_dedup_key_and_rule(2, "same-guid", "https://example.com/2", "Title 2", None)
        self.assertNotEqual(key1_guid, key2_guid)
        self.assertEqual(key1_guid, "guid:1:same-guid")
        self.assertEqual(key2_guid, "guid:2:same-guid")

        # 3. TP is source-scoped -> same title+published across two sources must yield different keys
        key1_tp, _ = generate_dedup_key_and_rule(1, None, None, "Same Title", "2026-06-02T12:00:00Z")
        key2_tp, _ = generate_dedup_key_and_rule(2, None, None, "Same Title", "2026-06-02T12:00:00Z")
        self.assertNotEqual(key1_tp, key2_tp)

if __name__ == "__main__":
    unittest.main()
