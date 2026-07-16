import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.database import safe_execute

def get_overall_fetch_success_rate(conn: sqlite3.Connection, start: str, end: str) -> Optional[float]:
    sql = """
        SELECT 
            SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS rate
        FROM fetch_attempt
        WHERE started_at >= :start AND started_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["rate"] if row else None

def get_run_success_rate(conn: sqlite3.Connection, start: str, end: str) -> Optional[float]:
    sql = """
        SELECT 
            SUM(succeeded_source_count) * 1.0 / NULLIF(SUM(attempted_source_count), 0) AS rate
        FROM fetch_run
        WHERE started_at >= :start AND started_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["rate"] if row else None

def get_error_categorization(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    sql = """
        SELECT 
            error_class,
            http_status,
            COUNT(*) AS error_count
        FROM fetch_attempt
        WHERE started_at >= :start AND started_at < :end
          AND outcome = 'failed'
        GROUP BY error_class, http_status
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_rolling_source_health(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    sql = """
        SELECT 
            source_id,
            health_status,
            consecutive_failures,
            last_http_status,
            last_error_class
        FROM source_state
    """
    cursor = safe_execute(conn, sql)
    return cursor.fetchall()

def get_ingest_volume(conn: sqlite3.Connection, start: str, end: str) -> int:
    sql = """
        SELECT COUNT(*) AS cnt 
        FROM source_item 
        WHERE fetched_at >= :start AND fetched_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["cnt"] if row else 0

def get_low_context_bypass_rate(conn: sqlite3.Connection, start: str, end: str) -> Optional[float]:
    sql = """
        SELECT 
            SUM(CASE WHEN sit.text_processing_status = 'low_context' THEN 1 ELSE 0 END) * 1.0 
            / NULLIF(COUNT(si.source_item_id), 0) AS rate
        FROM source_item si
        LEFT JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["rate"] if row else None

def get_low_context_reason_distribution(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    sql = """
        SELECT 
            sit.text_processing_reason,
            COUNT(*) AS reason_count
        FROM source_item si
        JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND sit.text_processing_status = 'low_context'
          AND sit.text_processing_reason IS NOT NULL
        GROUP BY sit.text_processing_reason
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()
