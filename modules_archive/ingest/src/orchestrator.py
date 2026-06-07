import asyncio
import json
import logging
import pathlib
from typing import List, Optional, Dict, Any

from .config import IngestConfig, SourceConfig
from .database import (
    get_connection,
    transaction,
    get_utc_now_iso8601,
    SourceStateRepository,
    FetchRunRepository,
    FetchAttemptRepository,
    SourceItemRepository,
    DedupMarkerRepository
)
from .fetcher import fetch_feed, FetchResult
from .parser import parse_feed_entries
from .scheduler import (
    is_source_due,
    should_skip_quarantined,
    apply_fetch_success,
    apply_fetch_failure
)
from .models import NormalizedItem, SourceExecutionResult, IngestRunSummary

logger = logging.getLogger("ingest.orchestrator")

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
    Orchestrates the fetch, parse, deduplicate, and persistence lifecycle for a single source.
    Opens its own separate SQLite connection and wraps updates in an atomic database transaction.
    """
    # 1. Open separate SQLite connection for this concurrent task
    conn = get_connection(db_path)
    try:
        # Initialize repositories
        state_repo = SourceStateRepository(conn)
        attempt_repo = FetchAttemptRepository(conn)
        item_repo = SourceItemRepository(conn)
        marker_repo = DedupMarkerRepository(conn)

        # 2. Retrieve prior state
        try:
            state_row = state_repo.get(source.id)
        except Exception:
            state_row = None
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

        # 3. Decision checking
        # disabled config filter
        if not source.enabled:
            return SourceExecutionResult(
                source_id=source.id,
                outcome="skipped",
                skip_reason="disabled",
                http_status=None,
                error_class=None,
                error_detail=None,
                new_item_count=0,
                dedup_matched_count=0,
                quarantined_now=False
            )

        # Force bypasses quarantine and due checks
        if not force:
            # Check quarantine status
            if should_skip_quarantined(quarantine_until, now_str):
                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="skipped",
                    skip_reason="quarantined",
                    http_status=None,
                    error_class=None,
                    error_detail=None,
                    new_item_count=0,
                    dedup_matched_count=0,
                    quarantined_now=False
                )

            # Check if source is due
            sc = config.schedule_classes.get(source.schedule_class)
            if not is_source_due(source, sc, last_success_at, now_str):
                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="skipped",
                    skip_reason="not_due",
                    http_status=None,
                    error_class=None,
                    error_detail=None,
                    new_item_count=0,
                    dedup_matched_count=0,
                    quarantined_now=False
                )

        # 4. Dry-run early return (stops before HTTP and DB modifications)
        if dry_run:
            return SourceExecutionResult(
                source_id=source.id,
                outcome="skipped",
                skip_reason="dry_run",
                http_status=None,
                error_class=None,
                error_detail=None,
                new_item_count=0,
                dedup_matched_count=0,
                quarantined_now=False
            )

        # 5. Execute Fetch (HTTP Request)
        attempt_started_at = get_utc_now_iso8601()
        fetch_result = await fetch_feed(
            xml_url=source.xml_url,
            etag=etag,
            last_modified=last_modified,
            semaphore=semaphore
        )
        attempt_ended_at = get_utc_now_iso8601()

        # 6. Process outcomes inside atomic transaction
        if fetch_result.error_class is None:
            # A. Success Flow (200 or 304)
            if fetch_result.status_code == 304:
                # 304 Cache hit -> update state & insert attempt
                with transaction(conn):
                    _, next_health, _ = apply_fetch_success(consecutive_failures)
                    state_repo.upsert(
                        source_id=source.id,
                        state_data={
                            "last_fetch_at": attempt_started_at,
                            "last_success_at": attempt_started_at,  # 304 counts as a successful poll
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
                        "attempt_started_at": attempt_started_at,
                        "attempt_ended_at": attempt_ended_at,
                        "retry_count": fetch_result.retry_count,
                        "http_status": 304,
                        "error_class": None,
                        "error_detail": None,
                        "outcome": "success",
                        "new_item_count": 0,
                        "dedup_matched_count": 0
                    })
                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="success",
                    skip_reason=None,
                    http_status=304,
                    error_class=None,
                    error_detail=None,
                    new_item_count=0,
                    dedup_matched_count=0,
                    quarantined_now=False
                )
            
            else:
                # 200 OK -> parse & persist items
                normalized_items = parse_feed_entries(source.id, fetch_result.content, attempt_ended_at)
                new_count = 0
                dedup_count = 0
                
                with transaction(conn):
                    # Dedup & insert loop
                    for item in normalized_items:
                        if marker_repo.exists(item.ingest_dedup_key):
                            dedup_count += 1
                        else:
                            item_id = item_repo.insert({
                                "source_id": item.source_id,
                                "source_item_guid": item.source_item_guid,
                                "canonical_url": item.canonical_url,
                                "title": item.title,
                                "summary": item.summary,
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
                            new_count += 1

                    # Update source state & attempt
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
                        "attempt_started_at": attempt_started_at,
                        "attempt_ended_at": attempt_ended_at,
                        "retry_count": fetch_result.retry_count,
                        "http_status": fetch_result.status_code,
                        "error_class": None,
                        "error_detail": None,
                        "outcome": "success",
                        "new_item_count": new_count,
                        "dedup_matched_count": dedup_count
                    })

                return SourceExecutionResult(
                    source_id=source.id,
                    outcome="success",
                    skip_reason=None,
                    http_status=fetch_result.status_code,
                    error_class=None,
                    error_detail=None,
                    new_item_count=new_count,
                    dedup_matched_count=dedup_count,
                    quarantined_now=False
                )

        else:
            # B. Failure Flow (transient timeout/5xx/network or non-transient 4xx/unexpected)
            if fetch_result.error_class == "unexpected_error":
                raise RuntimeError(f"Unexpected run-level error during fetch: {fetch_result.error_detail}")

            with transaction(conn):
                next_failures, next_health, next_quarantine = apply_fetch_failure(
                    consecutive_failures,
                    now_str
                )
                state_repo.upsert(
                    source_id=source.id,
                    state_data={
                        "last_fetch_at": attempt_started_at,
                        "last_success_at": last_success_at,  # preserve previous success timestamp
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
                    "attempt_started_at": attempt_started_at,
                    "attempt_ended_at": attempt_ended_at,
                    "retry_count": fetch_result.retry_count,
                    "http_status": fetch_result.status_code,
                    "error_class": fetch_result.error_class,
                    "error_detail": fetch_result.error_detail,
                    "outcome": "failed",
                    "new_item_count": 0,
                    "dedup_matched_count": 0
                })

            return SourceExecutionResult(
                source_id=source.id,
                outcome="failed",
                skip_reason=None,
                http_status=fetch_result.status_code,
                error_class=fetch_result.error_class,
                error_detail=fetch_result.error_detail,
                new_item_count=0,
                dedup_matched_count=0,
                quarantined_now=(next_health == "quarantined")
            )

    except Exception as e:
        # Unexpected errors are run-level failures, re-raise to fail the entire run
        logger.error(f"Critical orchestrator-level error in orchestrate_source for {source.id}: {str(e)}", exc_info=True)
        raise
    finally:
        conn.close()

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

    # 1. Filter enabled sources from config
    enabled_sources = [s for s in config.sources if s.enabled]

    # 2. Apply optional command-line filtering criteria
    target_sources = []
    for source in enabled_sources:
        # Check source ID filter
        if source_ids is not None and source.id not in source_ids:
            continue
        # Check fetch group filter
        if groups is not None and source.fetch_group not in groups:
            continue
        target_sources.append(source)

    # Calculate run scope representation
    if source_ids is not None:
        run_scope = f"sources:{sorted(source_ids)}"
    elif groups is not None:
        run_scope = f"groups:{sorted(groups)}"
    else:
        run_scope = "all"

    # 3. Insert fetch_run record to database if not dry-run
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

    # 4. Perform concurrent fetching with a bounded semaphore of 5
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

    # Execute all source fetches concurrently with failure isolation
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 5. Compile aggregate run statistics
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
        
        # Check if the coroutine raised an unhandled exception outside of orchestration
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

    # 6. Determine final run status
    if orchestrator_crashed:
        run_status = "failed"
    elif failed > 0:
        run_status = "partial_failure"
    else:
        run_status = "success"

    error_summary = "\n".join(error_classes) if error_classes else None

    # 7. Complete fetch_run in DB
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
