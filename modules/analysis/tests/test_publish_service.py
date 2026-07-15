import pytest
from unittest.mock import MagicMock
from modules.analysis.src.services.publish_service import PublishService

def test_publish_service_with_mock_data(empty_db_conn):
    conn = empty_db_conn
    now = "2026-07-15T12:00:00Z"

    # Seed data
    # Ingest
    conn.execute("""
        INSERT INTO source_item (source_item_id, source_id, title, published_at, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (301, 1, "Sighting Title", "2026-07-10T09:50:00Z", "2026-07-10T10:00:00Z", "key-301", "guid"))

    # Classify
    conn.execute("""
        INSERT INTO classification_result (
            source_item_id, topic_class, classification_confidence, content_density, model_name, prompt_version, classified_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "core", 0.95, "high", "test-model", "v1.0", "2026-07-10T10:10:00Z", now))

    # Curate
    conn.execute("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_reason, decision_actor, model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "approved", None, "looks good", "operator", "test-model", "v1.0", "2026-07-10T10:30:00Z", now, now))

    conn.execute("""
        INSERT INTO approved_content_record (
            parent_content_id, source_item_id, display_title, content_body, content_fingerprint, content_language_code, approved_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (501, 301, "Approved Display Title", "Body content", "fp-301", "en", "2026-07-10T10:30:00Z", now, now))

    # Translate (completed translation)
    conn.execute("""
        INSERT INTO translation_output (
            parent_content_id, source_item_id, language_code, display_title, content, source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (501, 301, "zh", "中文標題", "中文內容", "fp-301", "completed", "test-translator", "v1.0", "2026-07-10T11:00:00Z", now))

    # Publish
    conn.execute("""
        INSERT INTO publish_record (
            publish_record_id, source_item_id, slug, first_published_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (701, 301, "slug-301", "2026-07-10T11:15:00Z", now, now))

    conn.execute("""
        INSERT INTO publish_language_status (
            publish_record_id, language_code, publish_status, published_at, source_fingerprint, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (701, "zh", "published", "2026-07-10T11:15:00Z", "fp-301", now))

    conn.commit()

    service = PublishService(conn, target_languages=["en", "zh", "ja"])
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_publish_analysis(days=7)

    # 1. Assert overall metrics
    metrics = report["metrics"]
    assert metrics["publish_count"] == 1

    # 2. Breakdowns
    breakdowns = report["breakdowns"]
    assert len(breakdowns) == 3

    # Find zh
    zh = next(b for b in breakdowns if b["language_code"] == "zh")
    assert pytest.approx(zh["language_coverage_rate"]) == 1.0
    # Publish delay: 2026-07-10T11:15:00Z (published_at) - 2026-07-10T11:00:00Z (translated_at) = 900 seconds
    assert pytest.approx(zh["publish_delay_seconds"]["average"]) == 900.0
    assert pytest.approx(zh["publish_delay_seconds"]["median"]) == 900.0
    assert pytest.approx(zh["publish_delay_seconds"]["p90"]) == 900.0

    # Find ja
    ja = next(b for b in breakdowns if b["language_code"] == "ja")
    assert ja["language_coverage_rate"] == 0.0
    assert ja["publish_delay_seconds"]["average"] is None

    # Test Markdown report formatting
    report_md = service.format_markdown_report(report)
    assert "# Publishing Performance & Diagnostics Report" in report_md
    assert "Publish Count" in report_md
    assert "1" in report_md
    assert "zh" in report_md
    assert "900.00s" in report_md
