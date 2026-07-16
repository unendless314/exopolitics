import pytest
import json
import datetime
from unittest.mock import patch
from modules.analysis.src.services.funnel_calculator import FunnelCalculator

class MockDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.datetime(2026, 7, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)

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

    calculator = FunnelCalculator(conn, target_languages=["en", "zh", "ja"], maturation_offset_hours=2)

    with patch("modules.analysis.src.services.funnel_calculator.datetime.datetime", MockDateTime):
        report = calculator.run_funnel_analysis(days=7)

    # 1. Assert Schema & Basic window details
    assert report["report_type"] == "funnel"
    assert report["schema_version"] == "2.0.0"
    assert report["maturation_offset_hours"] == 2
    assert report["raw_window"]["start"] == "2026-07-08T12:00:00Z"
    assert report["raw_window"]["end"] == "2026-07-15T12:00:00Z"
    assert report["matured_window"]["start"] == "2026-07-08T10:00:00Z"
    assert report["matured_window"]["end"] == "2026-07-15T10:00:00Z"

    # 2. Assert raw_metrics
    raw_m = report["raw_metrics"]
    assert raw_m["total_ingested"] == 1
    assert raw_m["total_classified"] == 1
    assert raw_m["relevant_classified"] == 1
    assert raw_m["total_curated"] == 1
    assert raw_m["curation_approved"] == 1
    assert raw_m["total_translated"] == 1
    assert raw_m["total_published"] == 1

    raw_rb = raw_m["classification_readiness_breakdown"]
    assert raw_rb["total_classified"] == 1
    assert raw_rb["low_context_bypass"] == 0
    assert raw_rb["pending_classification"] == 0
    assert raw_rb["failed_text_processing"] == 0
    assert raw_rb["missing_text_processing"] == 0

    # 3. Assert matured_metrics
    matured_m = report["matured_metrics"]
    assert matured_m["total_ingested"] == 1
    assert matured_m["classification_rate"] == 1.0
    assert matured_m["curation_approval_rate"] == 1.0

    # 4. Assert raw_latency_metrics
    latency = report["raw_latency_metrics"]
    # E2E Pipeline Lead Time: 2026-07-10T11:15:00Z (published) - 2026-07-10T10:00:00Z (fetched) = 4500 seconds
    assert latency["pipeline_lead_time_seconds"]["average"] == 4500.0

    # 5. Assert published by language
    pub_langs = report["published_by_language"]
    zh_pub = next(l for l in pub_langs if l["language_code"] == "zh")
    assert zh_pub["published_count"] == 1
    assert zh_pub["coverage_rate"] == 1.0

    # Test Markdown report formatting
    report_md = calculator.format_markdown_report(report)
    assert "# Pipeline Funnel Conversion & Bottleneck Report" in report_md
    assert "Latency metrics include system initialization/historical ingestion data" in report_md
    assert "4500.00s" in report_md


def test_funnel_counts_exclude_orphaned_curation(empty_db_conn):
    conn = empty_db_conn
    timestamp = "2026-07-10T10:00:00Z"

    conn.execute("""
        INSERT INTO source_item (source_item_id, source_id, title, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (302, 1, "Orphaned curation", timestamp, "key-302", "guid"))
    conn.execute("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, raw_text_length,
            sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (302, "Body content", "default", 0, 0, "completed", 100, 100, timestamp, timestamp))
    conn.execute("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_actor,
            model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (302, "rejected", "reject_discard", "operator", "test-model", "v1.0", timestamp, timestamp, timestamp))
    conn.commit()

    calculator = FunnelCalculator(conn)
    with patch("modules.analysis.src.services.funnel_calculator.datetime.datetime", MockDateTime):
        report = calculator.run_funnel_analysis(days=7)

    assert report["raw_metrics"]["total_classified"] == 0
    assert report["raw_metrics"]["total_curated"] == 0
