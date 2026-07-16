import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.database import safe_execute

def get_publish_count(conn: sqlite3.Connection, start: str, end: str) -> int:
    sql = """
        SELECT COUNT(*) AS cnt
        FROM publish_record
        WHERE first_published_at >= :start AND first_published_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["cnt"] if row else 0

def get_language_coverage_rates(
    conn: sqlite3.Connection, start: str, end: str, target_languages_json: str
) -> List[sqlite3.Row]:
    sql = """
        SELECT
            lang.value AS language_code,
            COUNT(DISTINCT CASE WHEN pls.language_code = lang.value AND pls.publish_status = 'published' AND pls.source_fingerprint = acr.content_fingerprint THEN pr.source_item_id END) * 1.0
            / NULLIF(COUNT(DISTINCT acr.source_item_id), 0) AS coverage_rate
        FROM json_each(:target_languages_json) lang
        CROSS JOIN approved_content_record acr
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        JOIN curation_decision cd ON acr.source_item_id = cd.source_item_id
        LEFT JOIN publish_record pr ON acr.source_item_id = pr.source_item_id
        LEFT JOIN publish_language_status pls ON pr.publish_record_id = pls.publish_record_id AND pls.language_code = lang.value
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'approved'
        GROUP BY lang.value
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end, "target_languages_json": target_languages_json})
    return cursor.fetchall()

def get_publish_delays(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    sql = """
        SELECT 
            pls.language_code,
            CAST((strftime('%s', pls.published_at) - strftime('%s', tor.translated_at)) AS INTEGER) AS latency
        FROM publish_language_status pls
        JOIN publish_record pr ON pls.publish_record_id = pr.publish_record_id
        JOIN approved_content_record acr ON pr.source_item_id = acr.source_item_id
        JOIN translation_output tor ON acr.parent_content_id = tor.parent_content_id AND tor.language_code = pls.language_code
        JOIN source_item si ON pr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND pls.published_at IS NOT NULL
          AND tor.translated_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()
