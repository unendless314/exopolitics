import unittest
import time
from modules.ingest.src.normalize import (
    normalize_title,
    normalize_dedup_title,
    normalize_url,
    normalize_published_at,
)

class TestNormalizer(unittest.TestCase):
    def test_normalize_title_whitespace(self) -> None:
        self.assertEqual(normalize_title("  Simple Title  "), "Simple Title")
        self.assertEqual(normalize_title("Title\nwith\r\nnewlines and \t tabs"), "Title with newlines and tabs")
        self.assertEqual(normalize_title("   "), "")
        self.assertEqual(normalize_title(None), "")
        self.assertEqual(normalize_title(1234), "")

    def test_normalize_url(self) -> None:
        self.assertEqual(normalize_url("  https://example.com/some/path/  "), "https://example.com/some/path")
        self.assertEqual(normalize_url("HTTP://EXAMPLE.COM/"), "http://example.com/")
        self.assertEqual(normalize_url("https://example.com/path?query=1"), "https://example.com/path?query=1")
        self.assertEqual(normalize_url("https://example.com/path#fragment"), "https://example.com/path")
        self.assertEqual(normalize_url("https://example.com/path?query=1#frag"), "https://example.com/path?query=1")
        self.assertIsNone(normalize_url(""))
        self.assertIsNone(normalize_url("   "))
        self.assertIsNone(normalize_url(None))

    def test_normalize_url_strips_tracking_params(self) -> None:
        # utm_* family stripped, real query params kept
        self.assertEqual(
            normalize_url("https://example.com/path?utm_source=rss&utm_medium=feed&id=42"),
            "https://example.com/path?id=42"
        )
        # known click-tracking ids stripped
        self.assertEqual(
            normalize_url("https://example.com/path?fbclid=abc&gclid=def"),
            "https://example.com/path"
        )
        # tracking params mixed with content params; original order of kept params preserved
        self.assertEqual(
            normalize_url("https://example.com/p?a=1&utm_campaign=x&b=2"),
            "https://example.com/p?a=1&b=2"
        )
        # same article with and without tracking params must converge
        self.assertEqual(
            normalize_url("https://example.com/story?id=7&utm_source=a"),
            normalize_url("https://example.com/story?id=7")
        )

    def test_normalize_dedup_title(self) -> None:
        self.assertEqual(normalize_dedup_title("  The   Same   TITLE Here  "), "the same title here")
        self.assertEqual(normalize_dedup_title(None), "")
        self.assertEqual(normalize_dedup_title(1234), "")
        self.assertEqual(normalize_dedup_title("   "), "")
        # casefold and unicode folding make these equal
        self.assertEqual(normalize_dedup_title("“Dark” Comets"), normalize_dedup_title('"dark" comets'))
        # Google News publisher suffix intentionally retained
        self.assertNotEqual(
            normalize_dedup_title("Article Title - CNN"),
            normalize_dedup_title("Article Title")
        )

    def test_normalize_published_at_struct_time(self) -> None:
        st = time.struct_time((2026, 6, 2, 13, 16, 18, 1, 153, 0))
        self.assertEqual(normalize_published_at(st), "2026-06-02T13:16:18Z")

    def test_normalize_published_at_raw_string_fallback(self) -> None:
        self.assertEqual(normalize_published_at(None, "Tue, 02 Jun 2026 13:16:18 GMT"), "2026-06-02T13:16:18Z")
        self.assertEqual(normalize_published_at(None, "2026-06-02T13:16:18Z"), "2026-06-02T13:16:18Z")
        self.assertEqual(normalize_published_at(None, "2026-06-02T13:16:18+00:00"), "2026-06-02T13:16:18Z")
        self.assertEqual(normalize_published_at(None, "Tue, 02 Jun 2026 15:16:18 +0200"), "2026-06-02T13:16:18Z")
        self.assertIsNone(normalize_published_at(None, "not-a-date"))
        self.assertIsNone(normalize_published_at(None, ""))
        self.assertIsNone(normalize_published_at(None, None))

if __name__ == "__main__":
    unittest.main()
