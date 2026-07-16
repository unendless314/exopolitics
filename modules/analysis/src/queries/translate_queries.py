import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.database import safe_execute

def get_overall_translation_success_rate(conn: sqlite3.Connection, start: str, end: str) -> Optional[float]:
    """
    Calculates the overall translation success rate under event_time basis.
    Formula: Successful / (Successful + Failed) attempts in the window.
    """
    sql = """
        SELECT
            SUM(CASE WHEN translation_status = 'completed' THEN 1 ELSE 0 END) * 1.0
            / NULLIF(COUNT(CASE WHEN translation_status IN ('completed', 'failed') THEN 1 END), 0) AS rate
        FROM translation_output
        WHERE updated_at >= :start AND updated_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["rate"] if row else None

def get_overall_translation_completion_rate(
    conn: sqlite3.Connection, start: str, end: str, target_languages_json: str
) -> Optional[float]:
    """
    Calculates the overall translation completion rate under cohort basis.
    Formula from METRICS_CATALOG.md:
    Articles with all target translations completed / Total active approved articles.
    """
    sql = """
        SELECT
            COUNT(DISTINCT CASE WHEN completed_count = required_translation_count THEN parent_content_id END) * 1.0
            / NULLIF(COUNT(DISTINCT parent_content_id), 0) AS translation_completion_rate
        FROM (
            SELECT
                acr.parent_content_id,
                COUNT(DISTINCT CASE WHEN tor.translation_status = 'completed' AND tor.model_name != 'bypass' THEN tor.language_code END) AS completed_count,
                (SELECT COUNT(*) FROM json_each(:target_languages_json) WHERE value != acr.content_language_code) AS required_translation_count
            FROM approved_content_record acr
            JOIN source_item si ON acr.source_item_id = si.source_item_id
            JOIN curation_decision cd ON acr.source_item_id = cd.source_item_id
            LEFT JOIN translation_output tor ON acr.parent_content_id = tor.parent_content_id
                AND tor.source_fingerprint = acr.content_fingerprint
                AND tor.language_code IN (SELECT value FROM json_each(:target_languages_json) WHERE value != acr.content_language_code)
            WHERE si.fetched_at >= :start AND si.fetched_at < :end
              AND cd.curate_status = 'approved'
            GROUP BY acr.parent_content_id
        ) t;
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end, "target_languages_json": target_languages_json})
    row = cursor.fetchone()
    return row["translation_completion_rate"] if row else None

def get_overall_translation_latency(conn: sqlite3.Connection, start: str, end: str) -> Optional[float]:
    """
    Calculates the average overall translation latency (delay) under cohort basis.
    Formula: Average of (translated_at - approved_at) for non-bypass records.
    """
    sql = """
        SELECT
            AVG(CAST((strftime('%s', tor.translated_at) - strftime('%s', acr.approved_at)) AS INTEGER)) AS avg_latency
        FROM translation_output tor
        JOIN approved_content_record acr ON tor.parent_content_id = acr.parent_content_id
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND tor.model_name != 'bypass'
          AND tor.translated_at IS NOT NULL
          AND acr.approved_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["avg_latency"] if row else None

def get_translation_success_and_stale_rates(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Retrieves translation success and stale rates grouped by language_code (event_time basis).
    """
    sql = """
        SELECT
            language_code,
            SUM(CASE WHEN translation_status = 'completed' THEN 1 ELSE 0 END) * 1.0
            / NULLIF(COUNT(CASE WHEN translation_status IN ('completed', 'failed') THEN 1 END), 0) AS success_rate,
            SUM(CASE WHEN translation_status = 'stale' THEN 1 ELSE 0 END) * 1.0
            / NULLIF(COUNT(CASE WHEN translation_status IN ('completed', 'failed', 'stale') THEN 1 END), 0) AS stale_rate
        FROM translation_output
        WHERE updated_at >= :start AND updated_at < :end
        GROUP BY language_code
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_translation_completion_rates(
    conn: sqlite3.Connection, start: str, end: str, target_languages_json: str
) -> List[sqlite3.Row]:
    """
    Retrieves completion rates grouped by language_code (cohort basis).
    """
    sql = """
        SELECT
            lang.value AS language_code,
            COUNT(DISTINCT CASE WHEN tor.translation_status = 'completed' AND tor.source_fingerprint = acr.content_fingerprint THEN acr.parent_content_id END) * 1.0
            / NULLIF(COUNT(DISTINCT CASE WHEN acr.content_language_code != lang.value THEN acr.parent_content_id END), 0) AS completion_rate
        FROM json_each(:target_languages_json) lang
        CROSS JOIN approved_content_record acr
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        JOIN curation_decision cd ON acr.source_item_id = cd.source_item_id
        LEFT JOIN translation_output tor ON acr.parent_content_id = tor.parent_content_id 
            AND tor.language_code = lang.value
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'approved'
        GROUP BY lang.value
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end, "target_languages_json": target_languages_json})
    return cursor.fetchall()

def get_translation_latencies(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Retrieves average translation latencies grouped by language_code (cohort basis).
    """
    sql = """
        SELECT
            tor.language_code,
            AVG(CAST((strftime('%s', tor.translated_at) - strftime('%s', acr.approved_at)) AS INTEGER)) AS avg_latency
        FROM translation_output tor
        JOIN approved_content_record acr ON tor.parent_content_id = acr.parent_content_id
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND tor.model_name != 'bypass'
          AND tor.translated_at IS NOT NULL
          AND acr.approved_at IS NOT NULL
        GROUP BY tor.language_code
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_translation_char_volumes(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Retrieves translation character volumes grouped by language_code (cohort basis).
    """
    sql = """
        SELECT
            tor.language_code,
            SUM(LENGTH(acr.display_title) + LENGTH(acr.content_body)) AS char_volume
        FROM translation_output tor
        JOIN approved_content_record acr ON tor.parent_content_id = acr.parent_content_id
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND tor.model_name != 'bypass'
        GROUP BY tor.language_code
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()
