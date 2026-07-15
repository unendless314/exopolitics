import pytest
from unittest.mock import MagicMock
from modules.analysis.src.services.source_classifier import SourceQuadrantClassifier
from modules.analysis.src.services.source_service import SourceService
from modules.analysis.src.config import SourceMeta, CategoryMeta

def test_source_quadrant_classifier_safeguards_and_logic():
    classifier = SourceQuadrantClassifier(
        yield_threshold=0.10,
        relevance_threshold=0.40,
        fetch_isolation_threshold=0.50
    )

    # 1. Fetch Success < 50% isolation
    quadrant, flags = classifier.classify(
        fetch_success_rate=0.49,
        ingest_volume=10,
        relevance_rate=0.80,
        overall_yield=0.20,
        category_id=2
    )
    assert quadrant is None
    assert "CONNECTION_DIAGNOSTICS" in flags

    # 2. Ingest Volume = 0
    quadrant, flags = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=0,
        relevance_rate=None,
        overall_yield=None,
        category_id=2
    )
    assert quadrant is None
    assert "INSUFFICIENT_DATA" in flags

    # 3. None metrics
    quadrant, flags = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=5,
        relevance_rate=None,
        overall_yield=0.20,
        category_id=2
    )
    assert quadrant is None
    assert "INSUFFICIENT_DATA" in flags

    # 4. Authority categories (1 and 3)
    _, flags = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=5,
        relevance_rate=0.50,
        overall_yield=0.20,
        category_id=1
    )
    assert "AUTHORITY" in flags

    _, flags = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=5,
        relevance_rate=0.50,
        overall_yield=0.20,
        category_id=3
    )
    assert "AUTHORITY" in flags

    # 5. Quadrants
    # Golden Source: Yield >= 10% and Relevance >= 40%
    quadrant, _ = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=5,
        relevance_rate=0.40,
        overall_yield=0.10,
        category_id=2
    )
    assert quadrant == "golden_source"

    # Needle in a Haystack: Yield >= 10% and Relevance < 40%
    quadrant, _ = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=5,
        relevance_rate=0.39,
        overall_yield=0.10,
        category_id=2
    )
    assert quadrant == "needle_in_a_haystack"

    # Filtering Burden: Yield < 10% and Relevance >= 40%
    quadrant, _ = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=5,
        relevance_rate=0.40,
        overall_yield=0.09,
        category_id=2
    )
    assert quadrant == "filtering_burden"

    # Dead Weight: Yield < 10% and Relevance < 40%
    quadrant, _ = classifier.classify(
        fetch_success_rate=1.0,
        ingest_volume=5,
        relevance_rate=0.39,
        overall_yield=0.09,
        category_id=2
    )
    assert quadrant == "dead_weight"


def test_source_service_with_mock_data(empty_db_conn):
    conn = empty_db_conn
    now = "2026-07-15T12:00:00Z"

    # Seed fetch_run
    conn.execute("""
        INSERT INTO fetch_run (fetch_run_id, started_at, run_scope, trigger_type, run_status, due_source_count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (1, "2026-07-10T10:00:00Z", "all", "cron", "completed", 2))

    # Seed fetch attempts
    conn.executemany("""
        INSERT INTO fetch_attempt (fetch_run_id, source_id, started_at, ended_at, outcome)
        VALUES (?, ?, ?, ?, ?)
    """, [
        (1, 1, "2026-07-10T10:00:00Z", "2026-07-10T10:00:02Z", "success"),
        (1, 2, "2026-07-10T10:00:00Z", "2026-07-10T10:00:02Z", "failed")
    ])

    # Seed source items
    conn.executemany("""
        INSERT INTO source_item (source_item_id, source_id, title, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        (201, 1, "Government disclosure", "2026-07-10T10:00:00Z", "key-201", "guid"),
        (202, 1, "Low context article", "2026-07-11T11:00:00Z", "key-202", "guid")
    ])

    # Seed source item texts
    conn.executemany("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            raw_text_length, sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (201, "Body content", "default", 0, 0, "completed", None, 100, 100, now, now),
        (202, "", "default", 0, 0, "low_context", "too_short", 0, 0, now, now)
    ])

    # Seed classification
    conn.execute("""
        INSERT INTO classification_result (
            source_item_id, topic_class, classification_confidence, content_density, model_name, prompt_version, classified_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (201, "core", 0.95, "high", "test-model", "v1.0", "2026-07-10T10:05:00Z", now))

    # Seed curation decision
    conn.execute("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_reason, decision_actor, model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (201, "approved", None, "looks good", "operator", "test-model", "v1.0", "2026-07-10T10:10:00Z", now, now))

    # Seed approved content
    conn.execute("""
        INSERT INTO approved_content_record (
            source_item_id, display_title, content_body, content_fingerprint, content_language_code, approved_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (201, "Approved display", "Body content", "fp-201", "en", "2026-07-10T10:10:00Z", now, now))

    conn.commit()

    sources_meta = {
        1: SourceMeta(id=1, title="Official Source", xml_url="http://url-1", category_id=1, enabled=True, fetch_group=1, schedule_class="daily")
    }

    service = SourceService(conn, sources_meta=sources_meta)
    service.get_lookback_window = MagicMock(return_value=("2026-07-08T00:00:00Z", "2026-07-15T23:59:59Z"))

    report = service.run_sources_analysis(days=7)

    # Verify overall metrics
    metrics = report["metrics"]
    assert metrics["overall_fetch_success_rate"] == 0.50
    assert metrics["total_ingested_items"] == 2
    # low context bypass rate: 1/2 = 0.50
    assert metrics["low_context_bypass_rate"] == 0.50

    # Verify breakdown for Source 1
    breakdowns = report["breakdowns"]
    assert len(breakdowns) == 2  # Source 1 (configured) and Source 2 (found in DB)

    src_1 = next(b for b in breakdowns if b["source_id"] == 1)
    assert src_1["fetch_success_rate"] == 1.0
    assert src_1["ingest_volume"] == 2
    # Only 201 entered classification. Relevance rate = 1.0 (core)
    assert src_1["relevance_rate"] == 1.0
    # Curation approval: 201 is approved, so 1.0
    assert src_1["curation_approval_rate"] == 1.0
    # Overall yield: 1/2 = 0.50
    assert src_1["overall_yield"] == 0.50
    # Char volume classification: length("Government disclosure") = 21 + 100 = 121
    assert src_1["classification_character_volume_proxy"] == 121
    # Char volume curation: 201 has curation_decision, so 121
    assert src_1["curation_character_volume_proxy"] == 121
    # Topic breakdown
    assert src_1["topic_class_breakdown"]["core"] == 1.0
    # Decision model
    assert src_1["decision_model"]["quadrant"] == "golden_source"
    assert "AUTHORITY" in src_1["decision_model"]["analysis_flags"]

    # Reason distribution
    assert src_1["text_processing_reason_distribution"] == {"too_short": 1}

    # Verify Source 2 (Unknown source from DB)
    src_2 = next(b for b in breakdowns if b["source_id"] == 2)
    assert src_2["fetch_success_rate"] == 0.0
    assert src_2["ingest_volume"] == 0
    assert "INSUFFICIENT_DATA" in src_2["decision_model"]["analysis_flags"]

    # Test Markdown report formatting
    report_md = service.format_markdown_report(report)
    assert "# RSS Source Connection & Content Quality Report" in report_md
    assert "Official Source" in report_md
    assert "Unknown Source (ID: 2)" in report_md
