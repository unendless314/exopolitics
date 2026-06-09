import unittest
from modules.ingest.src.config import SanitizationProfile
from modules.ingest.src.sanitizer import sanitize_item, detect_html_markup, extract_raw_candidate

class TestSanitizer(unittest.TestCase):
    def setUp(self) -> None:
        self.default_profile = SanitizationProfile(
            input_preference=["summary", "content"],
            decode_entities=True,
            content_selectors=[],
            remove_selectors=["script", "style", "nav", "footer"],
            normalize_whitespace=True,
            collapse_blank_lines=True,
            max_length=500
        )

    def test_detect_html(self) -> None:
        self.assertTrue(detect_html_markup("<p>Hello World</p>"))
        self.assertTrue(detect_html_markup("Hello &amp; World"))
        self.assertFalse(detect_html_markup("Hello World"))

    def test_extract_raw_candidate(self) -> None:
        entry = {
            "summary": "This is summary",
            "content": [{"value": "This is content"}]
        }
        cand, field = extract_raw_candidate(entry, ["summary", "content"])
        self.assertEqual(cand, "This is summary")
        self.assertEqual(field, "summary")

        entry_no_sum = {
            "content": [{"value": "This is content"}]
        }
        cand, field = extract_raw_candidate(entry_no_sum, ["summary", "content"])
        self.assertEqual(cand, "This is content")
        self.assertEqual(field, "content")

    def test_sanitize_basic_html(self) -> None:
        entry = {
            "summary": "<div><p>Hello   World!</p><script>alert(1)</script></div>"
        }
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertEqual(res["sanitized_text"], "Hello World!")
        self.assertTrue(res["html_detected"])
        self.assertFalse(res["was_truncated"])

    def test_sanitize_truncation(self) -> None:
        profile = SanitizationProfile(
            input_preference=["summary"],
            max_length=10
        )
        entry = {"summary": "This is a very long text that should be truncated"}
        res = sanitize_item(entry, "My Title", profile)
        self.assertEqual(res["sanitized_text"], "This is a ")
        self.assertTrue(res["was_truncated"])

    def test_low_context_missing_body(self) -> None:
        entry = {}
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertTrue(res["is_low_context"])
        self.assertEqual(res["low_context_reason"], "missing_body")

    def test_low_context_post_cleanup_empty(self) -> None:
        entry = {"summary": "<script>var a = 1;</script>"}
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertTrue(res["is_low_context"])
        self.assertEqual(res["low_context_reason"], "post_cleanup_empty")

    def test_low_context_title_only(self) -> None:
        entry = {"summary": "  My Title  "}
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertTrue(res["is_low_context"])
        self.assertEqual(res["low_context_reason"], "title_only")

    def test_low_context_too_short(self) -> None:
        entry = {"summary": "Too short body text."} # len = 20 < 100
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertTrue(res["is_low_context"])
        self.assertEqual(res["low_context_reason"], "too_short")

    def test_low_context_title_heavy(self) -> None:
        # Title is 8 chars. Total text is 20 chars. Removing title leaves 12 chars (< 40)
        entry = {"summary": "My Title and some text"}
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertTrue(res["is_low_context"])
        self.assertEqual(res["low_context_reason"], "title_heavy")

    def test_low_context_mostly_links(self) -> None:
        # HTML with 80% link text
        entry = {
            "summary": "<a href='1'>Link Text 1</a> <a href='2'>Link Text 2</a> <a href='3'>Link Text 3</a> <a href='4'>Link Text 4</a> Plain"
        }
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertTrue(res["is_low_context"])
        self.assertEqual(res["low_context_reason"], "mostly_links")

    def test_low_context_template_heavy(self) -> None:
        # Contains multiple boilerplate phrases
        entry = {
            "summary": "This is a body of text that is long enough to pass the length check, but contains read more, click here, and follow us on which makes it template heavy."
        }
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertTrue(res["is_low_context"])
        self.assertEqual(res["low_context_reason"], "template_heavy")

if __name__ == "__main__":
    unittest.main()
