import pytest
import sqlite3
from modules.analysis.tests.generate_mock_db import DDL_STATEMENTS

@pytest.fixture
def empty_db_conn():
    """
    Provides an empty in-memory database with the full schema.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
    yield conn
    conn.close()

@pytest.fixture
def seeded_db_conn(empty_db_conn):
    """
    Provides a seeded in-memory database with test records for query logic verification.
    """
    conn = empty_db_conn
    # Let's seed some custom test data
    # Sources ID: 1, 2, 3
    # Fetch dates inside lookback window
    fetched_now = "2026-07-15T12:00:00Z"
    
    # 1. source_state
    conn.execute("""
        INSERT INTO source_state (source_id, health_status, consecutive_failures, updated_at)
        VALUES (1, 'healthy', 0, ?)
    """, (fetched_now,))

    # 2. source_item
    conn.executemany("""
        INSERT INTO source_item (source_item_id, source_id, title, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        (101, 1, "Core UAP Incident", "2026-07-10T10:00:00Z", "dup-101", "guid"),
        (102, 1, "Adjacent UFO Tech", "2026-07-11T11:00:00Z", "dup-102", "guid"),
        (103, 1, "Irrelevant Pop Science", "2026-07-12T12:00:00Z", "dup-103", "guid"),
        (104, 2, "Core Sighting", "2026-07-10T10:00:00Z", "dup-104", "guid"),
        (105, 3, "Out of window item", "2026-06-01T00:00:00Z", "dup-105", "guid")  # June 2026 (outside default lookback)
    ])

    # 3. source_item_text
    conn.executemany("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            raw_text_length, sanitized_text_length, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (101, "Text body for Core UAP", "default", 0, 0, "completed", None, 500, 500, fetched_now, fetched_now),
        (102, "Text body for Adjacent", "default", 0, 0, "completed", None, 600, 600, fetched_now, fetched_now),
        (103, "Text body for Irrelevant", "default", 0, 0, "completed", None, 700, 700, fetched_now, fetched_now),
        (104, "Text body for Core Sighting", "default", 0, 0, "completed", None, 800, 800, fetched_now, fetched_now),
        (105, "Text body for Out of window", "default", 0, 0, "completed", None, 400, 400, fetched_now, fetched_now)
    ])

    # 4. classification_result
    conn.executemany("""
        INSERT INTO classification_result (
            source_item_id, topic_class, classification_confidence, content_density, model_name, prompt_version, classified_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (101, "core", 0.95, "high", "test-model", "v1.0", "2026-07-10T10:05:00Z", fetched_now),
        (102, "adjacent", 0.85, "medium", "test-model", "v1.0", "2026-07-11T11:05:00Z", fetched_now),
        (103, "irrelevant", 0.75, "low", "test-model", "v1.0", "2026-07-12T12:05:00Z", fetched_now),
        (104, "core", 0.90, "medium", "test-model", "v1.0", "2026-07-10T10:05:00Z", fetched_now),
        (105, "core", 0.99, "high", "test-model", "v1.0", "2026-06-01T00:05:00Z", fetched_now)
    ])

    conn.commit()
    return conn
