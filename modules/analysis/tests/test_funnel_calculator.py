import pytest
import json
from unittest.mock import MagicMock
from modules.analysis.src.services.funnel_calculator import FunnelCalculator

def test_funnel_calculator_with_mock_data(empty_db_conn):
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

    # Seed fetch_run
    conn.execute("""
        INSERT INTO fetch_run (fetch_run_id, started_at, run_scope, trigger_type, run_status, due_source_count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (1, "2026-07-10T09:59:50Z", "all", "cron", "completed", 1))

    # Fetch attempts (1 execution)
    conn.execute("""
        INSERT INTO fetch_attempt (fetch_run_id, source_id, started_at, ended_at, outcome)
        VALUES (?, ?, ?, ?, ?)
    """, (1, 1, "2026-07-10T09:59:50Z", "2026-07-10T10:00:00Z", "success"))

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

    conn.execute("""
        INSERT INTO approved_content_record (
            parent_content_id, source_item_id, display_title, content_body, content_fingerprint, content_language_code, approved_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (501, 301, "Approved Display Title", "Body content", "fp-301", "en", "2026-07-10T10:30:00Z", now, now))

    # 4. Translation
    # Since source is "en", target is "zh" and "ja". Seed both as completed.
    conn.executemany("""
        INSERT INTO translation_output (
            parent_content_id, source_item_id, language_code, display_title, content, source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (501, 301, "zh", "中文標題", "中文內容", "fp-301", "completed", "test-translator", "v1.0", "2026-07-10T11:00:00Z", now),
        (501, 301, "ja", "日文標題", "日文內容", "fp-301", "completed", "test-translator", "v1.0", "2026-07-10T11:00:00Z", now)
    ])

    # 5. Publish
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

    calculator = FunnelCalculator(conn, target_languages=["en", "zh", "ja"])
    calculator.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = calculator.run_funnel_analysis(days=7)

    # 1. Assert Stage Counts
    # All stages should have count 1
    metrics = report["metrics"]
    assert metrics["total_ingested"] == 1
    assert metrics["total_classified"] == 1
    assert metrics["relevant_classified"] == 1
    assert metrics["total_curated"] == 1
    assert metrics["curation_approved"] == 1
    assert metrics["total_translated"] == 1
    assert metrics["total_published"] == 1

    # 2. Assert Conversions
    breakdowns = report["breakdowns"]
    for b in breakdowns:
        assert b["count"] == 1
        assert b["stage_conversion_rate"] == 1.0
        assert b["cumulative_yield"] == 1.0

    # 3. Assert Latencies
    # E2E Pipeline Lead Time: 2026-07-10T11:15:00Z (published) - 2026-07-10T10:00:00Z (fetched) = 4500 seconds
    assert metrics["pipeline_lead_time_seconds"]["average"] == 4500.0
    assert metrics["pipeline_lead_time_seconds"]["median"] == 4500.0
    assert metrics["pipeline_lead_time_seconds"]["p90"] == 4500.0

    latencies = report["stage_latency_breakdown_seconds"]
    # Feed Freshness Delay: 10:00:00 (fetched) - 09:50:00 (published) = 600s
    assert latencies["feed_freshness_delay"]["average"] == 600.0

    # Fetch Execution Latency: 10:00:00 (ended) - 09:59:50 (started) = 10s
    assert latencies["fetch_execution_latency"]["average"] == 10.0

    # Classification Delay: 10:10:00 (classified) - 10:00:00 (fetched) = 600s
    assert latencies["classification_delay"]["average"] == 600.0

    # Curation Delay: 10:30:00 (curated) - 10:10:00 (classified) = 1200s
    assert latencies["curation_delay"]["average"] == 1200.0

    # Translation Delay: 11:00:00 (translated) - 10:30:00 (approved) = 1800s
    assert latencies["translation_delay"]["average"] == 1800.0

    # Publish Delay: 11:15:00 (published) - 11:00:00 (translated) = 900s
    assert latencies["publish_delay"]["average"] == 900.0

    # 4. Language breakdowns
    pub_langs = report["published_by_language"]
    zh_pub = next(l for l in pub_langs if l["language_code"] == "zh")
    assert zh_pub["published_count"] == 1
    assert zh_pub["coverage_rate"] == 1.0

    ja_pub = next(l for l in pub_langs if l["language_code"] == "ja")
    assert ja_pub["published_count"] == 0
    assert ja_pub["coverage_rate"] == 0.0

    # Test Markdown report formatting
    report_md = calculator.format_markdown_report(report)
    assert "# Pipeline Funnel Conversion & Bottleneck Report" in report_md
    assert "E2E Pipeline Lead Time" in report_md
    assert "4500.00s" in report_md
