import pytest
from modules.analysis.src.queries import low_context_cohort_queries

WINDOW_START = "2026-07-08T00:00:00Z"
WINDOW_END = "2026-07-15T23:59:59Z"


def _seed_cohort_db(conn):
    """
    Seeds one representative per eligibility class:

    - 501/502/503: low-context, classified -> inside the cohort
    - 504: post_cleanup_empty -> terminal, excluded even though classified
    - 505: completed -> excluded (not low-context)
    - 506: failed -> terminal, excluded
    - 507: low-context without classification -> excluded
    - 508: low-context classified outside the window -> excluded
    """
    now = "2026-07-15T12:00:00Z"
    fetched = "2026-07-10T10:00:00Z"

    conn.executemany("""
        INSERT INTO source_item (source_item_id, source_id, title, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, 1, ?, ?, ?, 'guid')
    """, [
        (501, "Low Context Approved", fetched, "key-501"),
        (502, "Low Context Rejected", fetched, "key-502"),
        (503, "Low Context Uncurated", fetched, "key-503"),
        (504, "Empty After Cleanup", fetched, "key-504"),
        (505, "Completed Item", fetched, "key-505"),
        (506, "Failed Item", fetched, "key-506"),
        (507, "Low Context Unclassified", fetched, "key-507"),
        (508, "Old Low Context", "2026-06-01T00:00:00Z", "key-508"),
    ])

    conn.executemany("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, 'default', 0, 0, ?, ?, ?, ?, ?)
    """, [
        (501, "Snippet one", "low_context", "too_short", 20, now, now),
        (502, "https://example.com/a", "low_context", "mostly_links", 21, now, now),
        (503, "Title-like text", "low_context", "title_only", 15, now, now),
        (504, "", "low_context", "post_cleanup_empty", 0, now, now),
        (505, "Full body text", "completed", None, 500, now, now),
        (506, "", "failed", "missing_body", 0, now, now),
        (507, "Snippet two", "low_context", "too_short", 20, now, now),
        (508, "Old snippet", "low_context", "too_short", 20, now, now),
    ])

    conn.executemany("""
        INSERT INTO classification_result (
            source_item_id, topic_class, model_name, prompt_version, classified_at, created_at
        ) VALUES (?, ?, 'test-model', 'v1.0', ?, ?)
    """, [
        (501, "core", fetched, now),
        (502, "adjacent", fetched, now),
        (503, "unknown", fetched, now),
        (504, "core", fetched, now),
        (505, "core", fetched, now),
        (506, "core", fetched, now),
        (508, "core", "2026-06-01T00:05:00Z", now),
    ])

    conn.executemany("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_actor,
            model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, 'operator', 'test-model', 'v1.0', ?, ?, ?)
    """, [
        (501, "approved", "publish_link", fetched, now, now),
        (502, "rejected", "reject_discard", fetched, now, now),
        (505, "approved", "publish_summary", fetched, now, now),
    ])

    conn.commit()


def test_low_context_classified_cohort_metrics(empty_db_conn):
    _seed_cohort_db(empty_db_conn)

    metrics = low_context_cohort_queries.get_low_context_classified_cohort_metrics(
        empty_db_conn, WINDOW_START, WINDOW_END
    )

    # Only 501, 502, 503 meet the cohort predicate
    assert metrics["cohort_size"] == 3
    assert metrics["topic_class_distribution"] == {
        "core": 1,
        "adjacent": 1,
        "irrelevant": 0,
        "unknown": 1,
    }

    # Curation observed over the cohort only: 501 approved, 502 rejected, 503 uncurated
    assert metrics["curated_count"] == 2
    assert metrics["curation_approved_count"] == 1
    assert metrics["curation_approval_rate"] == pytest.approx(0.5)
    assert metrics["downstream_action_distribution"] == {
        "publish_link": 1,
        "publish_summary": 0,
        "edit_rewrite": 0,
        "reject_discard": 1,
    }


def test_low_context_classified_cohort_empty(empty_db_conn):
    metrics = low_context_cohort_queries.get_low_context_classified_cohort_metrics(
        empty_db_conn, WINDOW_START, WINDOW_END
    )

    assert metrics["cohort_size"] == 0
    assert metrics["topic_class_distribution"] == {
        "core": 0,
        "adjacent": 0,
        "irrelevant": 0,
        "unknown": 0,
    }
    assert metrics["curated_count"] == 0
    assert metrics["curation_approved_count"] == 0
    assert metrics["curation_approval_rate"] is None
    assert metrics["downstream_action_distribution"] == {
        "publish_link": 0,
        "publish_summary": 0,
        "edit_rewrite": 0,
        "reject_discard": 0,
    }
