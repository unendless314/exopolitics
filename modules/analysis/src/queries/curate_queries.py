import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.database import safe_execute

def get_curation_approval_rate(conn: sqlite3.Connection, start: str, end: str) -> Optional[float]:
    sql = """
        SELECT
            SUM(CASE WHEN cd.curate_status = 'approved' THEN 1 ELSE 0 END) * 1.0
            / NULLIF(COUNT(cd.source_item_id), 0) AS rate
        FROM curation_decision cd
        JOIN source_item si ON cd.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["rate"] if row else None

def get_curation_rejection_mix(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    sql = """
        SELECT
            cd.downstream_action,
            COUNT(*) AS count
        FROM curation_decision cd
        JOIN source_item si ON cd.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'rejected'
        GROUP BY cd.downstream_action
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_curation_char_volume_proxy(conn: sqlite3.Connection, start: str, end: str) -> int:
    sql = """
        SELECT
            SUM(LENGTH(si.title) + sit.sanitized_text_length) AS char_volume
        FROM source_item si
        JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        JOIN curation_decision cd ON si.source_item_id = cd.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["char_volume"] if row and row["char_volume"] is not None else 0

def get_curation_delays(conn: sqlite3.Connection, start: str, end: str) -> List[int]:
    sql = """
        SELECT CAST((strftime('%s', cd.curated_at) - strftime('%s', cr.classified_at)) AS INTEGER) AS latency
        FROM curation_decision cd
        JOIN classification_result cr ON cd.source_item_id = cr.source_item_id
        JOIN source_item si ON cd.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curated_at IS NOT NULL
          AND cr.classified_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return [row["latency"] for row in cursor.fetchall() if row["latency"] is not None]
