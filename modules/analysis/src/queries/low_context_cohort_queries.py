import sqlite3
from typing import Dict, Any
from modules.analysis.src.database import safe_execute

# Low-context classified cohort predicate (INGEST_LOW_CONTEXT_REFACTORING_PLAN.md section 4.2):
# items observed as low-context by ingest that remain classification-eligible
# (any reason except post_cleanup_empty) and have completed classification.
_COHORT_PREDICATE = """
    sit.text_processing_status = 'low_context'
    AND (
        sit.text_processing_reason IS NULL
        OR sit.text_processing_reason != 'post_cleanup_empty'
    )
    AND cr.classification_result_id IS NOT NULL
"""

_COHORT_JOINS = """
    FROM source_item si
    JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
    JOIN classification_result cr ON si.source_item_id = cr.source_item_id
"""

def get_low_context_classified_cohort_metrics(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, Any]:
    """
    Read-only observation of the low-context cohort that entered and completed classification.

    Reports, for the ingestion cohort window (source_item.fetched_at within [start, end)):
    - cohort_size: low-context eligible items holding a classification_result row
    - topic_class_distribution: count per topic_class within the cohort
    - curated_count / curation_approved_count / curation_approval_rate
    - downstream_action_distribution: count per curation downstream_action

    Diagnostic only: writes no canonical data and must not influence curation.
    """
    topic_sql = f"""
        SELECT cr.topic_class, COUNT(*) AS cnt
        {_COHORT_JOINS}
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND {_COHORT_PREDICATE}
        GROUP BY cr.topic_class
    """
    cursor = safe_execute(conn, topic_sql, {"start": start, "end": end})
    topic_distribution = {"core": 0, "adjacent": 0, "irrelevant": 0, "unknown": 0}
    for row in cursor.fetchall():
        topic_distribution[row["topic_class"]] = row["cnt"]
    cohort_size = sum(topic_distribution.values())

    curation_sql = f"""
        SELECT
            COUNT(cd.source_item_id) AS curated_count,
            SUM(CASE WHEN cd.curate_status = 'approved' THEN 1 ELSE 0 END) AS approved_count
        {_COHORT_JOINS}
        JOIN curation_decision cd ON si.source_item_id = cd.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND {_COHORT_PREDICATE}
    """
    cursor = safe_execute(conn, curation_sql, {"start": start, "end": end})
    row = cursor.fetchone()
    curated_count = row["curated_count"] or 0
    approved_count = row["approved_count"] or 0
    approval_rate = (approved_count / curated_count) if curated_count > 0 else None

    action_sql = f"""
        SELECT cd.downstream_action, COUNT(*) AS cnt
        {_COHORT_JOINS}
        JOIN curation_decision cd ON si.source_item_id = cd.source_item_id
        WHERE si.fetched_at >= :start AND si.fetched_at < :end
          AND {_COHORT_PREDICATE}
          AND cd.downstream_action IS NOT NULL
        GROUP BY cd.downstream_action
    """
    cursor = safe_execute(conn, action_sql, {"start": start, "end": end})
    action_distribution = {"publish_link": 0, "publish_summary": 0, "edit_rewrite": 0, "reject_discard": 0}
    for row in cursor.fetchall():
        action_distribution[row["downstream_action"]] = row["cnt"]

    return {
        "cohort_size": cohort_size,
        "topic_class_distribution": topic_distribution,
        "curated_count": curated_count,
        "curation_approved_count": approved_count,
        "curation_approval_rate": approval_rate,
        "downstream_action_distribution": action_distribution
    }
