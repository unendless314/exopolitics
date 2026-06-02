import unittest
from modules.ingest.src.parser import parse_feed_entries

class TestFeedParser(unittest.TestCase):
    def test_parse_valid_rss_feed(self) -> None:
        rss_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Mock Feed</title>
    <link>https://example.com</link>
    <item>
      <title>  Spotted Unidentified Object  </title>
      <link>https://example.com/item1#frag</link>
      <description>Item description content</description>
      <guid>unique-guid-1</guid>
      <pubDate>Tue, 02 Jun 2026 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Another Item</title>
      <link>https://example.com/item2</link>
      <pubDate>Tue, 02 Jun 2026 13:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""
        items = parse_feed_entries(source_id=5, xml_content=rss_xml, fetched_at="2026-06-02T14:00:00Z")
        
        self.assertEqual(len(items), 2)
        
        # Test Item 1 (has GUID, URL, description)
        item1 = items[0]
        self.assertEqual(item1.source_id, 5)
        self.assertEqual(item1.title, "Spotted Unidentified Object")
        self.assertEqual(item1.source_item_guid, "unique-guid-1")
        self.assertEqual(item1.canonical_url, "https://example.com/item1")  # Normalized (frag stripped)
        self.assertEqual(item1.summary, "Item description content")
        self.assertEqual(item1.published_at, "2026-06-02T12:00:00Z")
        self.assertEqual(item1.fetched_at, "2026-06-02T14:00:00Z")
        self.assertEqual(item1.dedup_rule, "guid")
        self.assertEqual(item1.ingest_dedup_key, "guid:5:unique-guid-1")

        # Test Item 2 (no GUID, should use URL rule)
        item2 = items[1]
        self.assertEqual(item2.source_id, 5)
        self.assertEqual(item2.title, "Another Item")
        self.assertIsNone(item2.source_item_guid)
        self.assertEqual(item2.canonical_url, "https://example.com/item2")
        self.assertEqual(item2.published_at, "2026-06-02T13:00:00Z")
        self.assertEqual(item2.dedup_rule, "url")
        self.assertEqual(item2.ingest_dedup_key, "url:https://example.com/item2")

if __name__ == "__main__":
    unittest.main()
