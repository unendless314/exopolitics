import asyncio
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from modules.ingest.src import sanitizer
from modules.ingest.src.config import SanitizationProfile, validate_and_load_config
from modules.ingest.src.database import SourceItemTextRepository, get_connection, run_migrations
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src.orchestrator import orchestrate_run
from modules.ingest.src.sanitizer import sanitize_item, detect_html_markup, extract_raw_candidate
from modules.ingest.tests import feed_samples, integration_helpers

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
        self.assertEqual(res["text_processing_status"], "failed")
        self.assertEqual(res["text_processing_reason"], "missing_body")

    def test_low_context_post_cleanup_empty(self) -> None:
        entry = {"summary": "<script>var a = 1;</script>"}
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertEqual(res["text_processing_status"], "low_context")
        self.assertEqual(res["text_processing_reason"], "post_cleanup_empty")

    def test_low_context_title_only(self) -> None:
        entry = {"summary": "  My Title  "}
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertEqual(res["text_processing_status"], "low_context")
        self.assertEqual(res["text_processing_reason"], "title_only")

    def test_low_context_too_short(self) -> None:
        entry = {"summary": "Too short body text."} # len = 20 < 100
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertEqual(res["text_processing_status"], "low_context")
        self.assertEqual(res["text_processing_reason"], "too_short")

    def test_low_context_title_heavy(self) -> None:
        # Title is 8 chars. Total text is 20 chars. Removing title leaves 12 chars (< 40)
        entry = {"summary": "My Title and some text"}
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertEqual(res["text_processing_status"], "low_context")
        self.assertEqual(res["text_processing_reason"], "title_heavy")

    def test_low_context_mostly_links(self) -> None:
        # HTML with 80% link text
        entry = {
            "summary": "<a href='1'>Link Text 1</a> <a href='2'>Link Text 2</a> <a href='3'>Link Text 3</a> <a href='4'>Link Text 4</a> Plain"
        }
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertEqual(res["text_processing_status"], "low_context")
        self.assertEqual(res["text_processing_reason"], "mostly_links")

    def test_low_context_template_heavy(self) -> None:
        # Contains multiple boilerplate phrases
        entry = {
            "summary": "This is a body of text that is long enough to pass the length check, but contains read more, click here, and follow us on which makes it template heavy."
        }
        res = sanitize_item(entry, "My Title", self.default_profile)
        self.assertEqual(res["text_processing_status"], "low_context")
        self.assertEqual(res["text_processing_reason"], "template_heavy")


class TestSanitizationFailureAccounting(unittest.TestCase):
    """Pipeline-level sanitization accounting contracts: sanitizer exceptions
    count once, missing bodies do not count as sanitization failures, and the
    failure statuses persist as specified. Moved from the former
    test_integration.py during the Phase 6 test-structure split; test logic
    and assertions are unchanged.
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
    def test_sanitization_failure_counted_once_when_fallback_insert_fails(self, mock_fetch) -> None:
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
            content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
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
            content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
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


if __name__ == "__main__":
    unittest.main()
