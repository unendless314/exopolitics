import unittest

from modules.ingest.src.parser import FeedParseError, parse_feed_entries
from modules.ingest.tests import feed_samples

SOURCE_ID = 42
FETCHED_AT = "2026-06-05T00:00:00Z"


def parse_sample(xml: str):
    return parse_feed_entries(SOURCE_ID, xml.encode("utf-8"), FETCHED_AT)


class TestParseFeedEntries(unittest.TestCase):
    def test_rss_basic_entry_fields(self) -> None:
        items = parse_sample(feed_samples.RSS_BASIC)

        self.assertEqual(len(items), 1)
        item, raw_entry = items[0]
        self.assertEqual(item.source_id, SOURCE_ID)
        self.assertEqual(item.fetched_at, FETCHED_AT)
        # Title whitespace is collapsed and trimmed.
        self.assertEqual(item.title, "First Item Title")
        self.assertEqual(item.source_item_guid, "rss-guid-001")
        # RFC-822 pubDate is normalized to the UTC second-precision form.
        self.assertEqual(item.published_at, "2026-06-01T12:00:00Z")
        # RSS <description> is the preferred body input.
        self.assertEqual(item.summary, "First item summary.")
        # The raw feedparser entry travels alongside for downstream use.
        self.assertEqual(raw_entry.get("guid"), "rss-guid-001")

    def test_rss_url_normalization_feeds_dedup_key(self) -> None:
        items = parse_sample(feed_samples.RSS_BASIC)

        item, _ = items[0]
        # Scheme/host lowercased, tracking params and fragment stripped,
        # trailing slash removed before the URL becomes the dedup identity.
        self.assertEqual(item.canonical_url, "https://example.com/article?id=42")
        self.assertEqual(item.dedup_rule, "url")
        self.assertEqual(item.ingest_dedup_key, "url:https://example.com/article?id=42")

    def test_atom_basic_entry_fields(self) -> None:
        items = parse_sample(feed_samples.ATOM_BASIC)

        self.assertEqual(len(items), 1)
        item, _ = items[0]
        self.assertEqual(item.title, "Atom Entry Title")
        self.assertEqual(item.source_item_guid, "urn:uuid:atom-001")
        self.assertEqual(item.canonical_url, "https://example.org/articles/1")
        self.assertEqual(item.dedup_rule, "url")
        # +08:00 offset is converted to UTC.
        self.assertEqual(item.published_at, "2026-06-02T12:00:00Z")
        self.assertEqual(item.summary, "Atom summary text.")

    def test_body_falls_back_to_content_when_summary_missing(self) -> None:
        items = parse_sample(feed_samples.ATOM_CONTENT_ONLY)

        item, _ = items[0]
        self.assertEqual(item.summary, "<p>Body from content element.</p>")

    def test_summary_preferred_over_content(self) -> None:
        items = parse_sample(feed_samples.ATOM_SUMMARY_AND_CONTENT)

        item, _ = items[0]
        self.assertEqual(item.summary, "Summary wins.")

    def test_enclosure_url_fallback_when_link_missing(self) -> None:
        items = parse_sample(feed_samples.RSS_ENCLOSURE_ONLY)

        item, _ = items[0]
        self.assertEqual(item.source_item_guid, "episode-009")
        self.assertEqual(item.canonical_url, "https://example.net/media/ep9.mp3")
        self.assertEqual(item.dedup_rule, "url")
        self.assertIsNone(item.summary)

    def test_guid_rule_when_link_missing(self) -> None:
        items = parse_sample(feed_samples.RSS_GUID_NO_LINK)

        item, _ = items[0]
        self.assertIsNone(item.canonical_url)
        self.assertEqual(item.dedup_rule, "guid")
        self.assertEqual(item.ingest_dedup_key, f"guid:{SOURCE_ID}:rss-guid-777")

    def test_tp_rule_when_only_title_and_date_present(self) -> None:
        items = parse_sample(feed_samples.RSS_TITLE_DATE_ONLY)

        item, _ = items[0]
        self.assertIsNone(item.canonical_url)
        self.assertIsNone(item.source_item_guid)
        self.assertEqual(item.dedup_rule, "tp")
        self.assertTrue(item.ingest_dedup_key.startswith(f"tp:{SOURCE_ID}:"))
        self.assertEqual(item.published_at, "2026-06-04T12:00:00Z")

    def test_missing_fields_normalize_to_defaults(self) -> None:
        items = parse_sample(feed_samples.RSS_MINIMAL_ITEM)

        item, _ = items[0]
        self.assertEqual(item.title, "")
        self.assertIsNone(item.source_item_guid)
        self.assertIsNone(item.canonical_url)
        self.assertIsNone(item.published_at)
        self.assertEqual(item.summary, "Only a description.")
        # With no url/guid/title+date identity, the fallback hash rule applies.
        self.assertEqual(item.dedup_rule, "fh")
        self.assertTrue(item.ingest_dedup_key.startswith(f"fh:{SOURCE_ID}:"))
        # A missing title is too short to emit a title-hash marker.
        self.assertEqual(item.extra_dedup_markers, ())

    def test_invalid_date_yields_none_published_at(self) -> None:
        items = parse_sample(feed_samples.RSS_INVALID_DATE)

        item, _ = items[0]
        self.assertIsNone(item.published_at)
        # The rest of the entry still normalizes and identifies via URL.
        self.assertEqual(item.dedup_rule, "url")

    def test_title_hash_marker_emitted_for_long_titles_only(self) -> None:
        long_title_item, _ = parse_sample(feed_samples.RSS_BASIC)[0]
        self.assertEqual(len(long_title_item.extra_dedup_markers), 1)
        marker_key, marker_rule = long_title_item.extra_dedup_markers[0]
        self.assertEqual(marker_rule, "th")
        self.assertTrue(marker_key.startswith("th:"))

        short_title_item, _ = parse_sample(feed_samples.RSS_SHORT_TITLE)[0]
        # "Live" normalizes below the title-hash length threshold.
        self.assertEqual(short_title_item.extra_dedup_markers, ())

    def test_valid_feed_with_zero_items_returns_empty_list(self) -> None:
        # Existing behavior: a parseable payload with no entries is not an
        # error; it yields zero items.
        self.assertEqual(parse_sample(feed_samples.RSS_EMPTY_CHANNEL), [])
        # Empty bytes are not reported as ill-formed by feedparser either.
        self.assertEqual(parse_feed_entries(SOURCE_ID, b"", FETCHED_AT), [])

    def test_malformed_payload_raises_feed_parse_error(self) -> None:
        # feedparser does not raise on ill-formed XML; it sets the bozo flag.
        # Per FETCH_EXECUTION.md, a bozo payload is a source-level parse
        # failure, so the parser raises FeedParseError.
        bad_payloads = (
            b"this is not xml at all",
            b'<rss version="2.0"><channel><title>x</title><item><title>unclosed',
        )
        for bad_payload in bad_payloads:
            with self.subTest(payload=bad_payload[:20]):
                with self.assertRaises(FeedParseError):
                    parse_feed_entries(SOURCE_ID, bad_payload, FETCHED_AT)

    def test_partial_entries_do_not_save_malformed_feed(self) -> None:
        # A truncated payload may still yield entries via feedparser, but the
        # bozo flag makes the whole payload a parse failure.
        truncated = feed_samples.RSS_BASIC.rsplit("</channel>", 1)[0].encode("utf-8")
        with self.assertRaises(FeedParseError):
            parse_feed_entries(SOURCE_ID, truncated, FETCHED_AT)

    def test_encoding_override_recovery_is_not_a_parse_error(self) -> None:
        # A well-formed feed declared us-ascii but containing UTF-8 bytes sets
        # feedparser's bozo flag via CharacterEncodingOverride. That condition
        # is recoverable — the entries are valid and must be processed normally
        # instead of failing the source.
        items = parse_sample(feed_samples.RSS_ENCODING_OVERRIDE)

        self.assertEqual(len(items), 1)
        item, _ = items[0]
        self.assertEqual(item.title, "Café")
        self.assertEqual(item.canonical_url, "https://example.com/cafe")
        self.assertEqual(item.dedup_rule, "url")


if __name__ == "__main__":
    unittest.main()
