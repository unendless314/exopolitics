import asyncio
import datetime
import logging
import pathlib
import sqlite3
from typing import List, Optional, Dict, Any, Tuple

from .config import IngestConfig, SourceConfig
from .database import (
    get_connection,
    transaction,
    get_utc_now_iso8601,
    SourceStateRepository,
    FetchRunRepository,
    FetchAttemptRepository,
    SourceItemRepository,
    SourceItemTextRepository,
    SourceItemRawRepository,
    DedupMarkerRepository
)
from .fetcher import fetch_feed, FetchResult
from .parser import parse_feed_entries
from .sanitizer import sanitize_item
from .scheduler import (
    is_source_due,
    should_skip_quarantined,
    apply_fetch_success,
    apply_fetch_failure
)
from .models import NormalizedItem

logger = logging.getLogger("ingest.orchestrator")


def add_days_to_iso8601(iso_str: str, days: int) -> str:
    """Helper to add N days to a UTC ISO-8601 string."""
    dt = datetime.datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    dt_new = dt + datetime.timedelta(days=days)
    return dt_new.strftime("%Y-%m-%dT%H:%M:%SZ")

# UNIQUE constraints that represent dedup identity collisions. Only conflicts
# on these constraints may be treated as a dedup race; any other IntegrityError
# (CHECK violations, FK failures, other UNIQUE constraints) is a real
# persistence bug and must surface as an item failure, not a silent skip.
_DEDUP_KEY_UNIQUE_TARGETS = ("ingest_dedup_marker.dedup_key", "source_item.ingest_dedup_key")

def _is_dedup_key_conflict(err: sqlite3.IntegrityError) -> bool:
    """True only when the error is a UNIQUE conflict on a dedup identity key."""
    msg = str(err)
    return "UNIQUE constraint failed" in msg and any(t in msg for t in _DEDUP_KEY_UNIQUE_TARGETS)

class SourceExecutionResult:
    def __init__(
        self,
        source_id: int,
        outcome: str,
        skip_reason: Optional[str] = None,
        http_status: Optional[int] = None,
        error_class: Optional[str] = None,
        error_detail: Optional[str] = None,
        new_item_count: int = 0,
        dedup_matched_count: int = 0,
        low_context_count: int = 0,
        sanitization_failure_count: int = 0,
        normalization_failure_count: int = 0,
        quarantined_now: bool = False
    ):
        self.source_id = source_id
        self.outcome = outcome
        self.skip_reason = skip_reason
        self.http_status = http_status
        self.error_class = error_class
        self.error_detail = error_detail
        self.new_item_count = new_item_count
        self.dedup_matched_count = dedup_matched_count
        self.low_context_count = low_context_count
        self.sanitization_failure_count = sanitization_failure_count
        self.normalization_failure_count = normalization_failure_count
        self.quarantined_now = quarantined_now


async def orchestrate_source(
    source: SourceConfig,
    config: IngestConfig,
    db_path: pathlib.Path,
    fetch_run_id: int,
    now_str: str,
    force: bool,
    dry_run: bool,
    semaphore: asyncio.Semaphore
) -> SourceExecutionResult:
    """
    Orchestrates the fetch, parse, deduplicate, sanitization, raw retention, and persistence
    lifecycle for a single source. Wrap updates in an atomic database transaction.
    """
    conn = get_connection(db_path)
    try:
        state_repo = SourceStateRepository(conn)
        attempt_repo = FetchAttemptRepository(conn)
        item_repo = SourceItemRepository(conn)
        text_repo = SourceItemTextRepository(conn)
        raw_repo = SourceItemRawRepository(conn)
        marker_repo = DedupMarkerRepository(conn)

        # Retrieve prior state
        state_row = None
        try:
            state_row = state_repo.get(source.id)
        except Exception as e:
            logger.warning(f"Failed to read state for source {source.id}: {e}")

        if state_row:
            consecutive_failures = state_row["consecutive_failures"]
            health_status = state_row["health_status"]
            etag = state_row["etag"]
            last_modified = state_row["last_modified"]
            quarantine_until = state_row["quarantine_until"]
            last_success_at = state_row["last_success_at"]
        else:
            consecutive_failures = 0
            health_status = "healthy"
            etag = None
            last_modified = None
            quarantine_until = None
            last_success_at = None

        # Check disabled status
        if not source.enabled:
            return SourceExecutionResult(
                source_id=source.id,
                outcome="skipped",
                skip_reason="disabled"
            )

        # Skip due and quarantine checks if force is active
        if not force:
            if should_skip_quarantined(quarantine_until, now_str):
                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="skipped",
                    skip_reason="quarantined"
                )

            sc = config.schedule_classes.get(source.schedule_class)
            if not is_source_due(source, sc, last_success_at, now_str):
                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="skipped",
                    skip_reason="not_due"
                )

        if dry_run:
            return SourceExecutionResult(
                source_id=source.id,
                outcome="skipped",
                skip_reason="dry_run"
            )

        # Execute HTTP Fetch
        attempt_started_at = get_utc_now_iso8601()
        fetch_result = await fetch_feed(
            xml_url=source.xml_url,
            etag=etag,
            last_modified=last_modified,
            timeout_seconds=float(source.request_timeout_seconds or 10),
            custom_headers=source.request_headers,
            semaphore=semaphore
        )
        attempt_ended_at = get_utc_now_iso8601()

        # Handle Fetch Outcomes
        if fetch_result.error_class is None:
            # 304 Cache Hit
            if fetch_result.status_code == 304:
                with transaction(conn):
                    state_repo.upsert(
                        source_id=source.id,
                        state_data={
                            "last_fetch_at": attempt_started_at,
                            "last_success_at": attempt_started_at,
                            "last_http_status": 304,
                            "etag": etag,
                            "last_modified": last_modified,
                            "consecutive_failures": 0,
                            "health_status": "healthy",
                            "quarantine_until": None,
                            "last_error_class": None,
                            "last_error_at": None
                        }
                    )
                    attempt_repo.insert({
                        "fetch_run_id": fetch_run_id,
                        "source_id": source.id,
                        "started_at": attempt_started_at,
                        "ended_at": attempt_ended_at,
                        "retry_count": fetch_result.retry_count,
                        "http_status": 304,
                        "error_class": None,
                        "error_detail": None,
                        "outcome": "success",
                        "new_item_count": 0,
                        "dedup_matched_count": 0,
                        "low_context_count": 0,
                        "sanitization_failure_count": 0,
                        "normalization_failure_count": 0
                    })
                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="success",
                    http_status=304
                )

            # 200 OK - Parse Feed
            try:
                parsed_entries = parse_feed_entries(source.id, fetch_result.content, attempt_ended_at)
            except Exception as parse_err:
                logger.error(f"Feed parse failure for source {source.id}: {parse_err}")
                # Record parse failure as source-level error
                with transaction(conn):
                    next_failures, next_health, next_quarantine = apply_fetch_failure(consecutive_failures, now_str)
                    state_repo.upsert(
                        source_id=source.id,
                        state_data={
                            "last_fetch_at": attempt_started_at,
                            "last_success_at": last_success_at,
                            "last_http_status": fetch_result.status_code,
                            "etag": None,
                            "last_modified": None,
                            "consecutive_failures": next_failures,
                            "health_status": next_health,
                            "quarantine_until": next_quarantine,
                            "last_error_class": "parse_error",
                            "last_error_at": attempt_ended_at
                        }
                    )
                    attempt_repo.insert({
                        "fetch_run_id": fetch_run_id,
                        "source_id": source.id,
                        "started_at": attempt_started_at,
                        "ended_at": attempt_ended_at,
                        "retry_count": fetch_result.retry_count,
                        "http_status": fetch_result.status_code,
                        "error_class": "parse_error",
                        "error_detail": f"ParseError: {parse_err}",
                        "outcome": "failed",
                        "new_item_count": 0,
                        "dedup_matched_count": 0,
                        "low_context_count": 0,
                        "sanitization_failure_count": 0,
                        "normalization_failure_count": 0
                    })
                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="failed",
                    http_status=fetch_result.status_code,
                    error_class="parse_error",
                    error_detail=str(parse_err)
                )

            new_item_count = 0
            dedup_matched_count = 0
            low_context_count = 0
            sanitization_failure_count = 0
            normalization_failure_count = 0

            # Process parsed items inside transaction
            with transaction(conn):
                for item, raw_entry in parsed_entries:
                    # Validate item requirements (title is required, ingest_dedup_key is required)
                    if not item.title or not item.ingest_dedup_key:
                        normalization_failure_count += 1
                        continue

                    # Deduplication check: duplicate when ANY key matches
                    # (primary key plus extra global markers such as title hash)
                    all_dedup_keys = [item.ingest_dedup_key] + [k for k, _ in item.extra_dedup_markers]
                    matched_marker = marker_repo.find_match(all_dedup_keys)
                    if matched_marker:
                        dedup_matched_count += 1
                        logger.info(
                            f"Item deduped in source {source.id}: "
                            f"matched_rule={matched_marker['dedup_rule']} "
                            f"matched_key={matched_marker['dedup_key']} "
                            f"skipped_title={item.title!r}"
                        )
                        continue

                    # Establish savepoint boundary for individual item persistence
                    conn.execute("SAVEPOINT item_tx")
                    item_failure_class = None
                    persisted = False
                    try:
                        # Insert source item
                        item_id = item_repo.insert({
                            "source_id": item.source_id,
                            "source_item_guid": item.source_item_guid,
                            "canonical_url": item.canonical_url,
                            "title": item.title,
                            "published_at": item.published_at,
                            "fetched_at": item.fetched_at,
                            "ingest_dedup_key": item.ingest_dedup_key,
                            "dedup_rule": item.dedup_rule
                        })
                        marker_repo.insert(
                            dedup_key=item.ingest_dedup_key,
                            dedup_rule=item.dedup_rule,
                            source_item_id=item_id
                        )
                        # Extra global markers (e.g. title hash) for cross-source dedup
                        for extra_key, extra_rule in item.extra_dedup_markers:
                            marker_repo.insert(
                                dedup_key=extra_key,
                                dedup_rule=extra_rule,
                                source_item_id=item_id
                            )

                        # Sanitization pipeline
                        san_profile = config.get_merged_sanitization_profile(source)
                        try:
                            san_result = sanitize_item(raw_entry, item.title, san_profile)
                            
                            # Save sanitization working text
                            text_repo.insert({
                                "source_item_id": item_id,
                                "sanitized_text": san_result["sanitized_text"],
                                "sanitization_method": san_result["sanitization_method"],
                                "html_detected": san_result["html_detected"],
                                "was_truncated": san_result["was_truncated"],
                                "text_processing_status": san_result["text_processing_status"],
                                "text_processing_reason": san_result["text_processing_reason"],
                                "raw_text_length": san_result["raw_text_length"],
                                "sanitized_text_length": san_result["sanitized_text_length"],
                                "reduction_ratio": san_result["reduction_ratio"]
                            })

                            if san_result["text_processing_status"] == "low_context":
                                low_context_count += 1

                            raw_payload = san_result.get("raw_payload", "")

                        except Exception as san_err:
                            logger.error(f"Sanitization pipeline failed for item in source {source.id}: {san_err}")
                            item_failure_class = "sanitization"
                            # Attempt fallback insertion
                            text_repo.insert({
                                "source_item_id": item_id,
                                "sanitized_text": "",
                                "sanitization_method": "error_fallback",
                                "html_detected": 0,
                                "was_truncated": 0,
                                "text_processing_status": "failed",
                                "text_processing_reason": "sanitizer_exception",
                                "raw_text_length": 0,
                                "sanitized_text_length": 0,
                                "reduction_ratio": 0.0
                            })
                            raw_payload = ""

                        # Raw retention policy
                        ret_policy = config.raw_retention
                        if ret_policy.default_days > 0:
                            expires_at = add_days_to_iso8601(item.fetched_at, ret_policy.default_days)
                            raw_repo.insert({
                                "source_item_id": item_id,
                                "raw_payload": str(raw_payload),
                                "retention_class": "default",
                                "expires_at": expires_at,
                                "created_at": item.fetched_at
                            })

                        # Commit the savepoint changes for this item
                        conn.execute("RELEASE item_tx")
                        persisted = True

                    except sqlite3.IntegrityError as int_err:
                        try:
                            conn.execute("ROLLBACK TO item_tx")
                            conn.execute("RELEASE item_tx")
                        except Exception as tx_err:
                            logger.error(f"Failed to rollback savepoint: {tx_err}")
                        if _is_dedup_key_conflict(int_err):
                            # Dedup race: another source inserted an overlapping
                            # dedup key (URL or title hash) after our pre-check.
                            # Treat as a dedup match, not an item failure.
                            logger.info(
                                f"Item skipped as cross-source duplicate (key collision) "
                                f"in source {source.id}: skipped_title={item.title!r} error={int_err}"
                            )
                            item_failure_class = "dedup_race"
                        else:
                            # Any other integrity error is a real persistence bug
                            # and must surface as an item failure below.
                            logger.error(f"Failed to persist item in source {source.id}: {int_err}")

                    except Exception as item_err:
                        logger.error(f"Failed to persist item in source {source.id}: {item_err}")
                        try:
                            conn.execute("ROLLBACK TO item_tx")
                            conn.execute("RELEASE item_tx")
                        except Exception as tx_err:
                            logger.error(f"Failed to rollback savepoint: {tx_err}")

                    # Update counts and metrics in one place
                    if item_failure_class == "sanitization":
                        sanitization_failure_count += 1
                    elif item_failure_class == "dedup_race":
                        dedup_matched_count += 1
                    elif not persisted:
                        normalization_failure_count += 1

                    if persisted:
                        new_item_count += 1

                # Update source state health and fetch attempt records
                state_repo.upsert(
                    source_id=source.id,
                    state_data={
                        "last_fetch_at": attempt_started_at,
                        "last_success_at": attempt_ended_at,
                        "last_http_status": fetch_result.status_code,
                        "etag": fetch_result.etag,
                        "last_modified": fetch_result.last_modified,
                        "consecutive_failures": 0,
                        "health_status": "healthy",
                        "quarantine_until": None,
                        "last_error_class": None,
                        "last_error_at": None
                    }
                )
                attempt_repo.insert({
                    "fetch_run_id": fetch_run_id,
                    "source_id": source.id,
                    "started_at": attempt_started_at,
                    "ended_at": attempt_ended_at,
                    "retry_count": fetch_result.retry_count,
                    "http_status": fetch_result.status_code,
                    "error_class": None,
                    "error_detail": None,
                    "outcome": "success",
                    "new_item_count": new_item_count,
                    "dedup_matched_count": dedup_matched_count,
                    "low_context_count": low_context_count,
                    "sanitization_failure_count": sanitization_failure_count,
                    "normalization_failure_count": normalization_failure_count
                })

            return SourceExecutionResult(
                source_id=source.id,
                outcome="success",
                http_status=fetch_result.status_code,
                new_item_count=new_item_count,
                dedup_matched_count=dedup_matched_count,
                low_context_count=low_context_count,
                sanitization_failure_count=sanitization_failure_count,
                normalization_failure_count=normalization_failure_count
            )

        else:
            # Fetch Failure Flow
            with transaction(conn):
                next_failures, next_health, next_quarantine = apply_fetch_failure(consecutive_failures, now_str)
                state_repo.upsert(
                    source_id=source.id,
                    state_data={
                        "last_fetch_at": attempt_started_at,
                        "last_success_at": last_success_at,
                        "last_http_status": fetch_result.status_code,
                        "etag": None,
                        "last_modified": None,
                        "consecutive_failures": next_failures,
                        "health_status": next_health,
                        "quarantine_until": next_quarantine,
                        "last_error_class": fetch_result.error_class,
                        "last_error_at": attempt_ended_at
                    }
                )
                attempt_repo.insert({
                    "fetch_run_id": fetch_run_id,
                    "source_id": source.id,
                    "started_at": attempt_started_at,
                    "ended_at": attempt_ended_at,
                    "retry_count": fetch_result.retry_count,
                    "http_status": fetch_result.status_code,
                    "error_class": fetch_result.error_class,
                    "error_detail": fetch_result.error_detail,
                    "outcome": "failed",
                    "new_item_count": 0,
                    "dedup_matched_count": 0,
                    "low_context_count": 0,
                    "sanitization_failure_count": 0,
                    "normalization_failure_count": 0
                })
            return SourceExecutionResult(
                source_id=source.id,
                outcome="failed",
                http_status=fetch_result.status_code,
                error_class=fetch_result.error_class,
                error_detail=fetch_result.error_detail,
                quarantined_now=(next_health == "quarantined")
            )

    except Exception as exc:
        # Failure isolation: catch unexpected exceptions so they don't halt other sources
        logger.error(f"Isolation failure in source {source.id}: {exc}", exc_info=True)
        try:
            with transaction(conn):
                next_failures, next_health, next_quarantine = apply_fetch_failure(consecutive_failures, now_str)
                state_repo.upsert(
                    source_id=source.id,
                    state_data={
                        "last_fetch_at": now_str,
                        "last_success_at": last_success_at if 'last_success_at' in locals() else None,
                        "last_http_status": None,
                        "etag": None,
                        "last_modified": None,
                        "consecutive_failures": next_failures,
                        "health_status": next_health,
                        "quarantine_until": next_quarantine,
                        "last_error_class": "unexpected_error",
                        "last_error_at": now_str
                    }
                )
                attempt_repo.insert({
                    "fetch_run_id": fetch_run_id,
                    "source_id": source.id,
                    "started_at": now_str,
                    "ended_at": now_str,
                    "retry_count": 0,
                    "http_status": None,
                    "error_class": "unexpected_error",
                    "error_detail": str(exc),
                    "outcome": "failed",
                    "new_item_count": 0,
                    "dedup_matched_count": 0,
                    "low_context_count": 0,
                    "sanitization_failure_count": 0,
                    "normalization_failure_count": 0
                })
        except Exception as db_exc:
            logger.error(f"Failed to record isolated crash to database for source {source.id}: {db_exc}")
        
        return SourceExecutionResult(
            source_id=source.id,
            outcome="failed",
            error_class="unexpected_error",
            error_detail=str(exc)
        )
    finally:
        conn.close()


class IngestRunSummary:
    def __init__(
        self,
        fetch_run_id: int,
        started_at: str,
        ended_at: str,
        run_scope: str,
        trigger_type: str,
        run_status: str,
        due_source_count: int,
        attempted_source_count: int,
        succeeded_source_count: int,
        failed_source_count: int,
        new_item_count: int,
        dedup_matched_count: int,
        quarantined_count: int,
        skipped_reasons: Dict[str, int],
        error_summary: Optional[str]
    ):
        self.fetch_run_id = fetch_run_id
        self.started_at = started_at
        self.ended_at = ended_at
        self.run_scope = run_scope
        self.trigger_type = trigger_type
        self.run_status = run_status
        self.due_source_count = due_source_count
        self.attempted_source_count = attempted_source_count
        self.succeeded_source_count = succeeded_source_count
        self.failed_source_count = failed_source_count
        self.new_item_count = new_item_count
        self.dedup_matched_count = dedup_matched_count
        self.quarantined_count = quarantined_count
        self.skipped_reasons = skipped_reasons
        self.error_summary = error_summary


async def orchestrate_run(
    config: IngestConfig,
    db_path: pathlib.Path,
    trigger_type: str = "scheduled",
    groups: Optional[List[int]] = None,
    source_ids: Optional[List[int]] = None,
    force: bool = False,
    dry_run: bool = False
) -> IngestRunSummary:
    """
    Executes a complete execution batch/run of the Ingest pipeline concurrently.
    Protects database integrity and schedules eligible feeds with failure isolation.
    """
    started_at = get_utc_now_iso8601()

    enabled_sources = [s for s in config.sources if s.enabled]

    # Filter sources
    target_sources = []
    for source in enabled_sources:
        if source_ids is not None and source.id not in source_ids:
            continue
        if groups is not None and source.fetch_group not in groups:
            continue
        target_sources.append(source)

    if source_ids is not None:
        run_scope = f"sources:{sorted(source_ids)}"
    elif groups is not None:
        run_scope = f"groups:{sorted(groups)}"
    else:
        run_scope = "all"

    # Create fetch_run record
    fetch_run_id = -1
    if not dry_run:
        conn = get_connection(db_path)
        try:
            run_repo = FetchRunRepository(conn)
            with transaction(conn):
                fetch_run_id = run_repo.create(
                    run_scope=run_scope,
                    trigger_type=trigger_type,
                    due_source_count=len(target_sources)
                )
        finally:
            conn.close()

    # Concurrent execution using a semaphore of 5
    semaphore = asyncio.Semaphore(5)
    tasks = [
        orchestrate_source(
            source=s,
            config=config,
            db_path=db_path,
            fetch_run_id=fetch_run_id,
            now_str=started_at,
            force=force,
            dry_run=dry_run,
            semaphore=semaphore
        )
        for s in target_sources
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Compile run statistics
    attempted = 0
    succeeded = 0
    failed = 0
    new_items = 0
    dedup_matched = 0
    quarantined = 0
    skipped_reasons: Dict[str, int] = {}
    error_classes: List[str] = []
    orchestrator_crashed = False

    for idx, res in enumerate(results):
        source = target_sources[idx]
        
        if isinstance(res, Exception):
            failed += 1
            attempted += 1
            orchestrator_crashed = True
            error_classes.append(f"Source {source.id} OrchestrationException: {str(res)}")
            continue

        if res.outcome == "skipped":
            reason = res.skip_reason or "unknown"
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
        elif res.outcome == "success":
            attempted += 1
            succeeded += 1
            new_items += res.new_item_count
            dedup_matched += res.dedup_matched_count
        elif res.outcome == "failed":
            attempted += 1
            failed += 1
            if res.error_class:
                error_classes.append(f"Source {source.id} failed with {res.error_class}: {res.error_detail}")
            if res.quarantined_now:
                quarantined += 1

    ended_at = get_utc_now_iso8601()

    if orchestrator_crashed:
        run_status = "failed"
    elif failed > 0:
        run_status = "partial_failure"
    else:
        run_status = "success"

    error_summary = "\n".join(error_classes) if error_classes else None

    # Complete fetch_run
    if not dry_run:
        conn = get_connection(db_path)
        try:
            run_repo = FetchRunRepository(conn)
            with transaction(conn):
                run_repo.update_completion(
                    fetch_run_id=fetch_run_id,
                    run_status=run_status,
                    attempted=attempted,
                    succeeded=succeeded,
                    failed=failed,
                    error_summary=error_summary
                )
        finally:
            conn.close()

    return IngestRunSummary(
        fetch_run_id=fetch_run_id,
        started_at=started_at,
        ended_at=ended_at,
        run_scope=run_scope,
        trigger_type=trigger_type,
        run_status=run_status,
        due_source_count=len(target_sources),
        attempted_source_count=attempted,
        succeeded_source_count=succeeded,
        failed_source_count=failed,
        new_item_count=new_items,
        dedup_matched_count=dedup_matched,
        quarantined_count=quarantined,
        skipped_reasons=skipped_reasons,
        error_summary=error_summary
    )
