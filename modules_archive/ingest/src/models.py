from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass(frozen=True)
class NormalizedItem:
    source_id: int
    source_item_guid: Optional[str]
    canonical_url: Optional[str]
    title: str
    summary: Optional[str]
    published_at: Optional[str]  # UTC ISO-8601 second-precision string: YYYY-MM-DDTHH:MM:SSZ
    fetched_at: str              # UTC ISO-8601 second-precision string: YYYY-MM-DDTHH:MM:SSZ
    ingest_dedup_key: str        # Prefix encoded dedup key
    dedup_rule: str              # 'guid', 'url', 'tp', 'fh'

@dataclass(frozen=True)
class SourceExecutionResult:
    source_id: int
    outcome: str                # 'success', 'failed', 'skipped'
    skip_reason: Optional[str]   # 'disabled', 'not_due', 'quarantined'
    http_status: Optional[int]
    error_class: Optional[str]
    error_detail: Optional[str]
    new_item_count: int
    dedup_matched_count: int
    quarantined_now: bool

@dataclass(frozen=True)
class IngestRunSummary:
    fetch_run_id: int
    started_at: str
    ended_at: str
    run_scope: str
    trigger_type: str
    run_status: str              # 'success', 'partial_failure', 'failed'
    due_source_count: int
    attempted_source_count: int
    succeeded_source_count: int
    failed_source_count: int
    new_item_count: int
    dedup_matched_count: int
    quarantined_count: int
    skipped_reasons: Dict[str, int]
    error_summary: Optional[str]
