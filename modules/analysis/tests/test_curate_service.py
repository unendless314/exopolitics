import pytest
from unittest.mock import MagicMock
from modules.analysis.src.services.curate_service import CurateService

def test_curate_service_with_mock_data(empty_db_conn):
    conn = empty_db_conn
    now = "2026-07-15T12:00:00Z"

    # Seed data
    # Ingest
    conn.execute("""
        INSERT INTO source_item (source_item_id, source_id, title, published_at, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (301, 1, "Title 1", "2026-07-10T09:50:00Z", "2026-07-10T10:00:00Z", "key-301", "guid"))

    conn.execute("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            raw_text_length, sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (301, "Body content", "default", 0, 0, "completed", None, 100, 100, now, now))

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
    """, (301, "rejected", "reject_discard", "no UAP content", "operator", "test-model", "v1.0", "2026-07-10T10:30:00Z", now, now))

    conn.commit()

    service = CurateService(conn)
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_curate_analysis(days=7)

    # 1. Assert overall metrics
    metrics = report["metrics"]
    # Curated approval rate: 0 approved, 1 rejected -> 0.0
    assert pytest.approx(metrics["curation_approval_rate"]) == 0.0
    # Curation character volume proxy: Title 1 (7) + Body content text (100) = 107
    assert metrics["curation_character_volume_proxy"] == 107
    # Curation delay: 2026-07-10T10:30:00Z (curated_at) - 2026-07-10T10:10:00Z (classified_at) = 1200 seconds
    assert pytest.approx(metrics["curation_delay_seconds"]["average"]) == 1200.0
    assert pytest.approx(metrics["curation_delay_seconds"]["median"]) == 1200.0
    assert pytest.approx(metrics["curation_delay_seconds"]["p90"]) == 1200.0

    # 2. Rejection mix
    rejection = report["curation_rejection_mix"]
    assert rejection["reject_discard"] == 1

    # Markdown report formatting
    report_md = service.format_markdown_report(report)
    assert "# Curation Performance & Diagnostics Report" in report_md
    assert "Curation Approval Rate" in report_md
    assert "0.00%" in report_md
    assert "reject_discard" in report_md
