import datetime
from typing import Optional, Tuple
from .config import SourceConfig, ScheduleClassConfig

def parse_utc_timestamp(timestamp_str: str) -> datetime.datetime:
    """Parses a UTC ISO-8601 string (YYYY-MM-DDTHH:MM:SSZ) to a timezone-aware datetime."""
    # Since we strictly use Z suffix and second-precision in STORAGE_SCHEMA.md
    # e.g., '2026-06-02T12:00:00Z'
    return datetime.datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)

def format_utc_timestamp(dt: datetime.datetime) -> str:
    """Formats a datetime to a UTC ISO-8601 string."""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def is_source_due(
    source: SourceConfig,
    schedule_class: Optional[ScheduleClassConfig],
    last_success_at: Optional[str],
    now_str: str
) -> bool:
    """
    Determines if a source is due for fetching based on schedule interval.
    If it has never successfully fetched, it is due immediately.
    """
    if last_success_at is None:
        return True
    
    if schedule_class is None:
        # Fallback if schedule class config is missing - default to due
        return True

    now_dt = parse_utc_timestamp(now_str)
    last_success_dt = parse_utc_timestamp(last_success_at)

    elapsed_minutes = (now_dt - last_success_dt).total_seconds() / 60.0
    return elapsed_minutes >= float(schedule_class.target_interval_minutes)

def should_skip_quarantined(
    quarantine_until: Optional[str],
    now_str: str
) -> bool:
    """
    Returns True if the source is currently under quarantine.
    Quarantine expires when now >= quarantine_until.
    """
    if not quarantine_until:
        return False

    now_dt = parse_utc_timestamp(now_str)
    quarantine_until_dt = parse_utc_timestamp(quarantine_until)

    return now_dt < quarantine_until_dt

def apply_fetch_success(consecutive_failures: int) -> Tuple[int, str, Optional[str]]:
    """
    Returns new state values upon successful fetch:
    consecutive_failures resets to 0, health_status to 'healthy', quarantine_until is cleared.
    """
    return 0, "healthy", None

def apply_fetch_failure(
    consecutive_failures: int,
    now_str: str
) -> Tuple[int, str, Optional[str]]:
    """
    Applies health transition rules from ERROR_POLICY.md:
    - 1-2 consecutive failures: health remains healthy.
    - 3-4 consecutive failures: health becomes degraded.
    - 5+ consecutive failures: health becomes quarantined (for 24 hours).
    
    Returns:
        Tuple[new_consecutive_failures, new_health_status, new_quarantine_until]
    """
    new_failures = consecutive_failures + 1
    
    if new_failures >= 5:
        now_dt = parse_utc_timestamp(now_str)
        quarantine_until_dt = now_dt + datetime.timedelta(hours=24)
        quarantine_until = format_utc_timestamp(quarantine_until_dt)
        return new_failures, "quarantined", quarantine_until
    elif new_failures >= 3:
        return new_failures, "degraded", None
    else:
        return new_failures, "healthy", None
