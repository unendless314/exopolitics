import sqlite3
from typing import List, Dict, Any, Optional
from modules.analysis.src.database import safe_execute

def get_overall_fetch_success_rate(conn: sqlite3.Connection, start: str, end: str) -> Optional[float]:
    """
    Calculates overall fetch success rate under event_time basis.
    """
    sql = """
        SELECT
            SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS rate
        FROM fetch_attempt
        WHERE started_at >= :start AND started_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    return row["rate"] if row else None

def get_overall_ingest_metrics(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, Any]:
    """
    Calculates overall ingested count and low-context bypass rate under cohort basis.
    """
    sql = """
        SELECT
            COUNT(si.source_item_id) AS total_ingested_items,
            SUM(CASE WHEN sit.text_processing_status = 'low_context' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(si.source_item_id), 0) AS low_context_bypass_rate
        FROM source_item si
        LEFT JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    if row:
        return {
            "total_ingested_items": row["total_ingested_items"] or 0,
            "low_context_bypass_rate": row["low_context_bypass_rate"]
        }
    return {
        "total_ingested_items": 0,
        "low_context_bypass_rate": None
    }

def get_sources_fetch_stats(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Gets fetch success rates grouped by source_id.
    """
    sql = """
        SELECT
            source_id,
            SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS fetch_success_rate
        FROM fetch_attempt
        WHERE started_at >= :start AND started_at < :end
        GROUP BY source_id
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_sources_cohort_stats(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Retrieves core metrics for all sources within the ingestion cohort lookback window.
    """
    sql = """
        SELECT
            si.source_id,
            COUNT(DISTINCT si.source_item_id) AS ingest_volume,
            COUNT(DISTINCT cr.source_item_id) AS classified_count,
            SUM(CASE WHEN cr.topic_class IN ('core', 'adjacent') THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS relevance_rate,
            SUM(CASE WHEN cd.curate_status = 'approved' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cd.source_item_id), 0) AS curation_approval_rate,
            SUM(CASE WHEN cd.curate_status = 'approved' THEN 1 ELSE 0 END) AS curate_approved_count,
            COUNT(DISTINCT acr.source_item_id) * 1.0 / NULLIF(COUNT(DISTINCT si.source_item_id), 0) AS overall_yield,

            SUM(CASE WHEN sit.text_processing_status = 'completed' THEN LENGTH(si.title) + sit.sanitized_text_length ELSE 0 END) AS classification_char_volume_proxy,
            SUM(CASE WHEN cd.source_item_id IS NOT NULL THEN LENGTH(si.title) + sit.sanitized_text_length ELSE 0 END) AS curation_char_volume_proxy,

            SUM(CASE WHEN cr.topic_class = 'core' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_core,
            SUM(CASE WHEN cr.topic_class = 'adjacent' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_adjacent,
            SUM(CASE WHEN cr.topic_class = 'irrelevant' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_irrelevant,
            SUM(CASE WHEN cr.topic_class = 'unknown' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(cr.source_item_id), 0) AS prop_unknown
        FROM source_item si
        LEFT JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        LEFT JOIN classification_result cr ON si.source_item_id = cr.source_item_id
        LEFT JOIN curation_decision cd ON si.source_item_id = cd.source_item_id
        LEFT JOIN approved_content_record acr ON si.source_item_id = acr.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
        GROUP BY si.source_id
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_sources_reason_distributions(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Gets reason distribution count of low-context items grouped by source_id.
    """
    sql = """
        SELECT
            si.source_id,
            sit.text_processing_reason,
            COUNT(*) AS reason_count
        FROM source_item si
        JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND sit.text_processing_reason IS NOT NULL
        GROUP BY si.source_id, sit.text_processing_reason
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

# Funnel Calculation SQL lists for latency and conversions
def get_funnel_counts(conn: sqlite3.Connection, start: str, end: str, target_languages: Optional[List[str]] = None) -> Dict[str, int]:
    """
    Gets the distinct counts of items reaching each stage of the funnel for the cohort window.
    """
    # 1. Ingested
    cursor = safe_execute(conn, "SELECT COUNT(DISTINCT source_item_id) AS cnt FROM source_item WHERE fetched_at >= :start AND fetched_at < :end", {"start": start, "end": end})
    ingested = cursor.fetchone()["cnt"] or 0

    # 1.1 Low context count (not a main stage, but needed for metrics)
    cursor = safe_execute(conn, """
        SELECT COUNT(DISTINCT si.source_item_id) AS cnt
        FROM source_item si
        JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end AND sit.text_processing_status = 'low_context'
    """, {"start": start, "end": end})
    low_context_bypass = cursor.fetchone()["cnt"] or 0

    # 2. Classified
    cursor = safe_execute(conn, """
        SELECT COUNT(DISTINCT cr.source_item_id) AS cnt
        FROM classification_result cr
        JOIN source_item si ON cr.source_item_id = si.source_item_id
        JOIN source_item_text sit ON cr.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND sit.text_processing_status = 'completed'
    """, {"start": start, "end": end})
    classified = cursor.fetchone()["cnt"] or 0

    # 2.1 Classified Relevant (relevance tracking)
    cursor = safe_execute(conn, """
        SELECT COUNT(DISTINCT cr.source_item_id) AS cnt
        FROM classification_result cr
        JOIN source_item si ON cr.source_item_id = si.source_item_id
        JOIN source_item_text sit ON cr.source_item_id = sit.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cr.topic_class IN ('core', 'adjacent')
          AND sit.text_processing_status = 'completed'
    """, {"start": start, "end": end})
    classified_relevant = cursor.fetchone()["cnt"] or 0

    # 3. Curated
    cursor = safe_execute(conn, """
        SELECT COUNT(DISTINCT cd.source_item_id) AS cnt
        FROM curation_decision cd
        JOIN source_item si ON cd.source_item_id = si.source_item_id
        JOIN source_item_text sit ON cd.source_item_id = sit.source_item_id
        JOIN classification_result cr ON cd.source_item_id = cr.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND sit.text_processing_status = 'completed'
          AND cr.topic_class IN ('core', 'adjacent')
    """, {"start": start, "end": end})
    curated = cursor.fetchone()["cnt"] or 0

    # 4. Approved
    cursor = safe_execute(conn, """
        SELECT COUNT(DISTINCT acr.source_item_id) AS cnt
        FROM approved_content_record acr
        JOIN curation_decision cd ON acr.source_item_id = cd.source_item_id
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        JOIN source_item_text sit ON acr.source_item_id = sit.source_item_id
        JOIN classification_result cr ON acr.source_item_id = cr.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'approved'
          AND sit.text_processing_status = 'completed'
          AND cr.topic_class IN ('core', 'adjacent')
    """, {"start": start, "end": end})
    approved = cursor.fetchone()["cnt"] or 0

    # 5. Translated (Requires at least one completed translation matching master fingerprint)
    sql_translated = """
        SELECT COUNT(DISTINCT acr.source_item_id) AS cnt
        FROM approved_content_record acr
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        JOIN curation_decision cd ON acr.source_item_id = cd.source_item_id
        JOIN translation_output tor ON acr.parent_content_id = tor.parent_content_id
        JOIN source_item_text sit ON acr.source_item_id = sit.source_item_id
        JOIN classification_result cr ON acr.source_item_id = cr.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'approved'
          AND tor.translation_status = 'completed'
          AND tor.source_fingerprint = acr.content_fingerprint
          AND sit.text_processing_status = 'completed'
          AND cr.topic_class IN ('core', 'adjacent')
    """
    cursor = safe_execute(conn, sql_translated, {"start": start, "end": end})
    translated = cursor.fetchone()["cnt"] or 0

    # 6. Published (Requires at least one language to be in 'published' status matching master fingerprint)
    sql_published = """
        SELECT COUNT(DISTINCT pr.source_item_id) AS cnt
        FROM publish_record pr
        JOIN curation_decision cd ON pr.source_item_id = cd.source_item_id
        JOIN approved_content_record acr ON pr.source_item_id = acr.source_item_id
        JOIN publish_language_status pls ON pr.publish_record_id = pls.publish_record_id
        JOIN source_item si ON pr.source_item_id = si.source_item_id
        JOIN source_item_text sit ON pr.source_item_id = sit.source_item_id
        JOIN classification_result cr ON pr.source_item_id = cr.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'approved'
          AND pls.publish_status = 'published'
          AND pls.source_fingerprint = acr.content_fingerprint
          AND sit.text_processing_status = 'completed'
          AND cr.topic_class IN ('core', 'adjacent')
    """
    cursor = safe_execute(conn, sql_published, {"start": start, "end": end})
    published = cursor.fetchone()["cnt"] or 0

    return {
        "ingested": ingested,
        "low_context_bypass": low_context_bypass,
        "classified": classified,
        "classified_relevant": classified_relevant,
        "curated": curated,
        "approved": approved,
        "translated": translated,
        "published": published
    }

def get_published_by_language_stats(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """
    Retrieves counts of published items by language code.
    """
    sql = """
        SELECT
            pls.language_code,
            COUNT(DISTINCT pr.source_item_id) AS published_count
        FROM publish_language_status pls
        JOIN publish_record pr ON pls.publish_record_id = pr.publish_record_id
        JOIN approved_content_record acr ON pr.source_item_id = acr.source_item_id
        JOIN curation_decision cd ON pr.source_item_id = cd.source_item_id
        JOIN source_item si ON pr.source_item_id = si.source_item_id
        JOIN source_item_text sit ON pr.source_item_id = sit.source_item_id
        JOIN classification_result cr ON pr.source_item_id = cr.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'approved'
          AND pls.publish_status = 'published'
          AND pls.source_fingerprint = acr.content_fingerprint
          AND sit.text_processing_status = 'completed'
          AND cr.topic_class IN ('core', 'adjacent')
        GROUP BY pls.language_code
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchall()

def get_total_approved_articles_count(conn: sqlite3.Connection, start: str, end: str) -> int:
    """
    Gets the denominator for language coverage rate.
    """
    sql = """
        SELECT COUNT(DISTINCT acr.source_item_id) AS cnt
        FROM approved_content_record acr
        JOIN curation_decision cd ON acr.source_item_id = cd.source_item_id
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        JOIN source_item_text sit ON acr.source_item_id = sit.source_item_id
        JOIN classification_result cr ON acr.source_item_id = cr.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cd.curate_status = 'approved'
          AND sit.text_processing_status = 'completed'
          AND cr.topic_class IN ('core', 'adjacent')
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return cursor.fetchone()["cnt"] or 0

# Latency Query Lists
def get_e2e_latencies(conn: sqlite3.Connection, start: str, end: str) -> List[int]:
    sql = """
        SELECT CAST((strftime('%s', pr.first_published_at) - strftime('%s', si.fetched_at)) AS INTEGER) AS latency
        FROM publish_record pr
        JOIN source_item si ON pr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND pr.first_published_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return [row["latency"] for row in cursor.fetchall() if row["latency"] is not None]

def get_feed_freshness_delays(conn: sqlite3.Connection, start: str, end: str) -> List[int]:
    sql = """
        SELECT CAST((strftime('%s', si.fetched_at) - strftime('%s', si.published_at)) AS INTEGER) AS latency
        FROM source_item si
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND si.published_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return [row["latency"] for row in cursor.fetchall() if row["latency"] is not None]

def get_fetch_execution_latencies(conn: sqlite3.Connection, start: str, end: str) -> List[int]:
    # Event-time basis
    sql = """
        SELECT CAST((strftime('%s', fa.ended_at) - strftime('%s', fa.started_at)) AS INTEGER) AS latency
        FROM fetch_attempt fa
        WHERE fa.started_at >= :start AND fa.started_at < :end
          AND fa.ended_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return [row["latency"] for row in cursor.fetchall() if row["latency"] is not None]

def get_classification_delays(conn: sqlite3.Connection, start: str, end: str) -> List[int]:
    sql = """
        SELECT CAST((strftime('%s', cr.classified_at) - strftime('%s', si.fetched_at)) AS INTEGER) AS latency
        FROM classification_result cr
        JOIN source_item si ON cr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND cr.classified_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return [row["latency"] for row in cursor.fetchall() if row["latency"] is not None]

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

def get_translation_delays(conn: sqlite3.Connection, start: str, end: str) -> List[int]:
    sql = """
        SELECT CAST((strftime('%s', tor.translated_at) - strftime('%s', acr.approved_at)) AS INTEGER) AS latency
        FROM translation_output tor
        JOIN approved_content_record acr ON tor.parent_content_id = acr.parent_content_id
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND tor.model_name != 'bypass'
          AND tor.translated_at IS NOT NULL
          AND acr.approved_at IS NOT NULL
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    return [row["latency"] for row in cursor.fetchall() if row["latency"] is not None]

def get_publish_delays(conn: sqlite3.Connection, start: str, end: str) -> List[int]:
    sql = """
        SELECT CAST((strftime('%s', pls.published_at) - strftime('%s', tor.translated_at)) AS INTEGER) AS latency
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
    return [row["latency"] for row in cursor.fetchall() if row["latency"] is not None]

def get_classification_readiness_breakdown(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, int]:
    sql = """
    SELECT
        COUNT(si.source_item_id) AS total_ingested,
        SUM(CASE WHEN sit.text_processing_status = 'low_context' THEN 1 ELSE 0 END) AS low_context_bypass,
        SUM(CASE WHEN sit.text_processing_status = 'completed' AND cr.source_item_id IS NOT NULL THEN 1 ELSE 0 END) AS total_classified,
        SUM(CASE WHEN sit.text_processing_status = 'completed' AND cr.source_item_id IS NULL THEN 1 ELSE 0 END) AS pending_classification,
        SUM(CASE WHEN sit.text_processing_status = 'failed' THEN 1 ELSE 0 END) AS failed_text_processing,
        SUM(CASE WHEN sit.source_item_id IS NULL THEN 1 ELSE 0 END) AS missing_text_processing
    FROM source_item si
    LEFT JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
    LEFT JOIN classification_result cr ON si.source_item_id = cr.source_item_id
    WHERE si.fetched_at >= :start AND si.fetched_at < :end
    """
    cursor = safe_execute(conn, sql, {"start": start, "end": end})
    row = cursor.fetchone()
    if row:
        return {
            "total_ingested": row["total_ingested"] or 0,
            "low_context_bypass": row["low_context_bypass"] or 0,
            "total_classified": row["total_classified"] or 0,
            "pending_classification": row["pending_classification"] or 0,
            "failed_text_processing": row["failed_text_processing"] or 0,
            "missing_text_processing": row["missing_text_processing"] or 0
        }
    return {
        "total_ingested": 0,
        "low_context_bypass": 0,
        "total_classified": 0,
        "pending_classification": 0,
        "failed_text_processing": 0,
        "missing_text_processing": 0
    }

def get_data_quality_anomalies(conn: sqlite3.Connection, start: str, end: str) -> List[Dict[str, Any]]:
    anomalies = []

    # 1. ORPHANED_CLASSIFICATION
    sql_orphaned_class = """
    SELECT cr.source_item_id
    FROM classification_result cr
    JOIN source_item si ON cr.source_item_id = si.source_item_id
    LEFT JOIN source_item_text sit ON cr.source_item_id = sit.source_item_id
    WHERE si.fetched_at >= :start AND si.fetched_at < :end
      AND (sit.source_item_id IS NULL OR sit.text_processing_status != 'completed')
    ORDER BY cr.source_item_id
    """
    cursor = safe_execute(conn, sql_orphaned_class, {"start": start, "end": end})
    samples = [row["source_item_id"] for row in cursor.fetchall()]
    if samples:
        anomalies.append({
            "code": "ORPHANED_CLASSIFICATION",
            "count": len(samples),
            "item_samples": samples[:10]
        })

    # 2. ORPHANED_CURATION
    sql_orphaned_curate = """
    SELECT cd.source_item_id
    FROM curation_decision cd
    JOIN source_item si ON cd.source_item_id = si.source_item_id
    LEFT JOIN classification_result cr ON cd.source_item_id = cr.source_item_id
    WHERE si.fetched_at >= :start AND si.fetched_at < :end
      AND (cr.source_item_id IS NULL OR cr.topic_class NOT IN ('core', 'adjacent'))
    ORDER BY cd.source_item_id
    """
    cursor = safe_execute(conn, sql_orphaned_curate, {"start": start, "end": end})
    samples = [row["source_item_id"] for row in cursor.fetchall()]
    if samples:
        anomalies.append({
            "code": "ORPHANED_CURATION",
            "count": len(samples),
            "item_samples": samples[:10]
        })

    # 3. MISSING_TEXT_RECORD
    sql_missing_text = """
    SELECT si.source_item_id
    FROM source_item si
    LEFT JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
    WHERE si.fetched_at >= :start AND si.fetched_at < :end
      AND sit.source_item_id IS NULL
    ORDER BY si.source_item_id
    """
    cursor = safe_execute(conn, sql_missing_text, {"start": start, "end": end})
    samples = [row["source_item_id"] for row in cursor.fetchall()]
    if samples:
        anomalies.append({
            "code": "MISSING_TEXT_RECORD",
            "count": len(samples),
            "item_samples": samples[:10]
        })

    # 4. UNKNOWN_TEXT_PROCESSING_STATUS
    sql_unknown_status = """
    SELECT sit.source_item_id
    FROM source_item_text sit
    JOIN source_item si ON sit.source_item_id = si.source_item_id
    WHERE si.fetched_at >= :start AND si.fetched_at < :end
      AND sit.text_processing_status NOT IN ('completed', 'low_context', 'failed')
    ORDER BY sit.source_item_id
    """
    cursor = safe_execute(conn, sql_unknown_status, {"start": start, "end": end})
    samples = [row["source_item_id"] for row in cursor.fetchall()]
    if samples:
        anomalies.append({
            "code": "UNKNOWN_TEXT_PROCESSING_STATUS",
            "count": len(samples),
            "item_samples": samples[:10]
        })

    return anomalies
