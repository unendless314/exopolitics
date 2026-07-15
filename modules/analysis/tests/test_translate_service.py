import pytest
import json
from unittest.mock import MagicMock
from modules.analysis.src.services.translate_service import TranslateService

def test_translate_service_with_mock_data(empty_db_conn):
    conn = empty_db_conn
    now = "2026-07-15T12:00:00Z"

    # Seed data
    # 1. Ingest
    conn.execute("""
        INSERT INTO source_item (source_item_id, source_id, title, published_at, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (301, 1, "Sighting Title", "2026-07-10T09:50:00Z", "2026-07-10T10:00:00Z", "key-301", "guid"))

    # Ingest text status completed
    conn.execute("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            raw_text_length, sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "Body content", "default", 0, 0, "completed", None, 100, 100, now, now))

    # 2. Classify
    conn.execute("""
        INSERT INTO classification_result (
            source_item_id, topic_class, classification_confidence, content_density, model_name, prompt_version, classified_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "core", 0.95, "high", "test-model", "v1.0", "2026-07-10T10:10:00Z", now))

    # 3. Curate
    conn.execute("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_reason, decision_actor, model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "approved", None, "looks good", "operator", "test-model", "v1.0", "2026-07-10T10:30:00Z", now, now))

    # Approved content record: title length = 22, body length = 12, total content length = 34
    conn.execute("""
        INSERT INTO approved_content_record (
            parent_content_id, source_item_id, display_title, content_body, content_fingerprint, content_language_code, approved_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (501, 301, "Approved Display Title", "Body content", "fp-301", "en", "2026-07-10T10:30:00Z", now, now))

    # 4. Translation attempts (one success in zh, one fail in ja, one stale in ja)
    # Target languages are en, zh, ja. Source is en, so required translations are zh, ja.
    conn.executemany("""
        INSERT INTO translation_output (
            parent_content_id, source_item_id, language_code, display_title, content, source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (501, 301, "zh", "中文標題", "中文內容", "fp-301", "completed", "test-translator", "v1.0", "2026-07-10T11:00:00Z", "2026-07-10T11:00:00Z"),
        (501, 301, "ja", None, None, "fp-301", "failed", "test-translator", "v1.0", None, "2026-07-10T11:00:00Z")
    ])

    conn.commit()

    service = TranslateService(conn, target_languages=["en", "zh", "ja"])
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_translate_analysis(days=7)

    # 1. Assert overall metrics
    metrics = report["metrics"]
    # Overall translation success rate: 1 completed, 1 failed -> 1 / 2 = 0.50
    assert pytest.approx(metrics["overall_translation_success_rate"]) == 0.50
    # Overall completion rate: 0.0 because not all targets are completed (only zh is completed, ja is failed)
    assert pytest.approx(metrics["overall_translation_completion_rate"]) == 0.0
    # Average latency: 2026-07-10T11:00:00Z (translated_at) - 2026-07-10T10:30:00Z (approved_at) = 1800s
    assert pytest.approx(metrics["average_latency_seconds"]) == 1800.0

    # 2. Assert language breakdowns
    breakdowns = report["breakdowns"]
    assert len(breakdowns) == 3 # en, zh, ja

    # Find zh
    zh = next(b for b in breakdowns if b["language_code"] == "zh")
    assert pytest.approx(zh["translation_success_rate"]) == 1.0
    assert pytest.approx(zh["translation_completion_rate"]) == 1.0 # 1 article where source != zh, which is completed for zh
    assert pytest.approx(zh["average_latency_seconds"]) == 1800.0
    assert zh["stale_rate"] == 0.0
    assert zh["translation_character_volume_proxy"] == 34 # Approved Display Title (22) + Body content (12) = 34

    # Find ja
    ja = next(b for b in breakdowns if b["language_code"] == "ja")
    assert pytest.approx(ja["translation_success_rate"]) == 0.0
    assert pytest.approx(ja["translation_completion_rate"]) == 0.0
    assert ja["average_latency_seconds"] is None
    assert ja["stale_rate"] == 0.0
    assert ja["translation_character_volume_proxy"] == 34 # failed attempt still has a row in translation_output and is not bypass, so counted as recorded workload

    # Find en
    en = next(b for b in breakdowns if b["language_code"] == "en")
    # en is the source language of the article, so:
    # no translation output row for en -> success, completion, latency, stale are None, proxy is 0
    assert en["translation_success_rate"] is None
    assert en["translation_completion_rate"] is None
    assert en["average_latency_seconds"] is None
    assert en["stale_rate"] is None
    assert en["translation_character_volume_proxy"] == 0

    # Test Markdown report formatting
    report_md = service.format_markdown_report(report)
    assert "# Translation Performance & Queue Report" in report_md
    assert "Overall Translation Success Rate" in report_md
    assert "50.00%" in report_md
    assert "1800.00s" in report_md

def test_translate_service_empty_db(empty_db_conn):
    service = TranslateService(empty_db_conn)
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_translate_analysis(days=7)
    metrics = report["metrics"]
    assert metrics["overall_translation_success_rate"] is None
    assert metrics["overall_translation_completion_rate"] is None
    assert metrics["average_latency_seconds"] is None
    assert len(report["breakdowns"]) == 3
    for b in report["breakdowns"]:
        assert b["translation_success_rate"] is None
        assert b["translation_completion_rate"] is None
        assert b["average_latency_seconds"] is None
        assert b["stale_rate"] is None
        assert b["translation_character_volume_proxy"] == 0
