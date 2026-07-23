import pytest
from unittest.mock import MagicMock
from modules.analysis.src.services.ingest_service import IngestService

def test_ingest_service_with_mock_data(empty_db_conn):
    conn = empty_db_conn
    now = "2026-07-15T12:00:00Z"

    # Seed source state
    conn.execute("""
        INSERT INTO source_state (source_id, health_status, consecutive_failures, last_http_status, last_error_class, updated_at)
        VALUES (1, 'healthy', 0, 200, NULL, ?)
    """, (now,))

    # Seed fetch run
    conn.execute("""
        INSERT INTO fetch_run (fetch_run_id, started_at, run_scope, trigger_type, run_status, due_source_count, attempted_source_count, succeeded_source_count, failed_source_count)
        VALUES (1, "2026-07-10T10:00:00Z", "all", "cron", "completed", 1, 2, 1, 1)
    """)

    # Seed fetch attempt
    conn.executemany("""
        INSERT INTO fetch_attempt (fetch_run_id, source_id, started_at, ended_at, outcome, error_class, http_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        (1, 1, "2026-07-10T10:00:00Z", "2026-07-10T10:00:05Z", "success", None, 200),
        (1, 2, "2026-07-10T10:00:00Z", "2026-07-10T10:00:05Z", "failed", "http_error_5xx", 503)
    ])

    # Seed source item and text
    conn.executemany("""
        INSERT INTO source_item (source_item_id, source_id, title, published_at, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        (301, 1, "Item 1", "2026-07-10T09:50:00Z", "2026-07-10T10:00:00Z", "key-301", "guid"),
        (302, 1, "Item 2", "2026-07-10T09:50:00Z", "2026-07-10T10:00:00Z", "key-302", "guid")
    ])

    conn.executemany("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            raw_text_length, sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (301, "Body content", "default", 0, 0, "completed", None, 100, 100, now, now),
        (302, "Snippet", "default", 0, 0, "low_context", "too_short", 10, 10, now, now)
    ])

    conn.commit()

    service = IngestService(conn)
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_ingest_analysis(days=7)

    # 1. Assert overall metrics
    assert report["report_type"] == "ingest_diagnostics"
    assert report["schema_version"] == "2.0.0"
    metrics = report["metrics"]
    # Fetch Success Rate: 1 success, 1 failed -> 1/2 = 0.50
    assert pytest.approx(metrics["overall_fetch_success_rate"]) == 0.50
    # Run Success Rate: 1 succeeded, 1 failed source count -> 1/2 = 0.50
    assert pytest.approx(metrics["run_success_rate"]) == 0.50
    # Ingest Volume: 2 items
    assert metrics["ingest_volume"] == 2
    # Low-Context Observation Rate: 1 low_context, 1 completed -> 1/2 = 0.50
    assert pytest.approx(metrics["low_context_observation_rate"]) == 0.50

    # 2. Error Categorization
    errors = report["error_categorization"]
    assert len(errors) == 1
    assert errors[0]["error_class"] == "http_error_5xx"
    assert errors[0]["http_status"] == 503
    assert errors[0]["error_count"] == 1

    # 3. Rolling Source Health
    healths = report["rolling_source_health"]
    assert len(healths) == 1
    assert healths[0]["source_id"] == 1
    assert healths[0]["health_status"] == "healthy"

    # 4. Low-Context reason distribution
    reasons = report["low_context_reason_distribution"]
    assert reasons["too_short"] == 1

    # Markdown report formatting
    report_md = service.format_markdown_report(report)
    assert "# Ingestion Performance & Diagnostics Report" in report_md
    assert "Overall Fetch Success Rate" in report_md
    assert "50.00%" in report_md
    assert "too_short" in report_md
    # Renamed quality-observation labels; no bypass semantics
    assert "Low-Context Observation Rate" in report_md
    assert "Low-Context Reason Distribution" in report_md
    assert "Bypass" not in report_md
