import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.database import safe_execute

def get_overall_classify_metrics(conn: sqlite3.Connection, start: str, end: str) -> Optional[sqlite3.Row]:
    """
    Retrieves overall classification metrics within the lookback window.
    Filters by source_item.fetched_at (cohort basis).
    """
    sql = """
        SELECT
            COUNT(cr.source_item_id) AS total_classified,
            SUM(CASE WHEN cr.topic_class IN ('core', 'adjacent') THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS relevance_rate,
            AVG(cr.classification_confidence) AS average_confidence,
            SUM(CASE WHEN cr.topic_class = 'core' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_core,
            SUM(CASE WHEN cr.topic_class = 'adjacent' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_adjacent,
            SUM(CASE WHEN cr.topic_class = 'irrelevant' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_irrelevant,
            SUM(CASE WHEN cr.topic_class = 'unknown' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_unknown
        FROM classification_result cr
        JOIN source_item si ON cr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchone()

def get_source_classify_breakdowns(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Retrieves classification breakdown grouped by source_id.
    Filters by source_item.fetched_at (cohort basis).
    """
    sql = """
        SELECT
            si.source_id,
            COUNT(cr.source_item_id) AS classify_volume,
            SUM(CASE WHEN cr.topic_class IN ('core', 'adjacent') THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS relevance_rate,
            AVG(cr.classification_confidence) AS average_confidence,
            SUM(CASE WHEN cr.content_density = 'low' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS density_low_rate,
            SUM(CASE WHEN cr.content_density = 'medium' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS density_medium_rate,
            SUM(CASE WHEN cr.content_density = 'high' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS density_high_rate,
            SUM(CASE WHEN cr.topic_class = 'core' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_core,
            SUM(CASE WHEN cr.topic_class = 'adjacent' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_adjacent,
            SUM(CASE WHEN cr.topic_class = 'irrelevant' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_irrelevant,
            SUM(CASE WHEN cr.topic_class = 'unknown' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_unknown
        FROM classification_result cr
        JOIN source_item si ON cr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
        GROUP BY si.source_id
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_source_char_volumes(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Retrieves the classification character volume proxy grouped by source_id.
    Filters by source_item.fetched_at (cohort basis).
    Formula: SUM(length(si.title) + sit.sanitized_text_length) where text_processing_status = 'completed'.
    """
    sql = """
        SELECT
            si.source_id,
            SUM(LENGTH(si.title) + sit.sanitized_text_length) AS char_volume
        FROM source_item si
        JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND sit.text_processing_status = 'completed'
        GROUP BY si.source_id
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()
