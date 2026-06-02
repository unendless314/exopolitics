import argparse
import asyncio
import json
import os
import pathlib
import sys
from typing import List, Optional

from .config import load_config
from .validator import validate_config
from .database import run_migrations, get_connection, SourceStateRepository
from .orchestrator import orchestrate_run

DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
DEFAULT_CONFIG_DIR = pathlib.Path(__file__).resolve().parent.parent / "config"
DEFAULT_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / "migrations"

def get_args_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest Module Command-Line Interface (UAP Aggregation System)"
    )
    parser.add_argument(
        "--config-dir",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_DIR,
        help="Path to ingest configuration directory containing categories.yaml and sources.yaml"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")

    # 1. validate subcommand
    subparsers.add_parser("validate", help="Validate sources and categories configuration schemas")

    # 2. migrate subcommand
    migrate_parser = subparsers.add_parser("migrate", help="Execute SQLite database schema migrations")
    migrate_parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        default=DEFAULT_DB_PATH,
        help="Custom SQLite canonical database path"
    )

    # 3. fetch subcommand
    fetch_parser = subparsers.add_parser("fetch", help="Run scheduled or manual ingestion batches")
    fetch_parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        default=DEFAULT_DB_PATH,
        help="Custom SQLite canonical database path"
    )
    fetch_parser.add_argument(
        "--groups",
        type=int,
        nargs="+",
        help="Filter execution shards by specific fetch group numbers"
    )
    fetch_parser.add_argument(
        "--source-ids",
        type=int,
        nargs="+",
        help="Filter execution by specific source ID numbers"
    )
    fetch_parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass schedule interval due verification and active quarantines (respects enabled flag)"
    )
    fetch_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Predict and list which sources will be fetched without network or database writes"
    )
    fetch_parser.add_argument(
        "--trigger-type",
        choices=["scheduled", "manual", "recovery"],
        default="manual",
        help="The trigger type associated with this fetch run"
    )
    fetch_parser.add_argument(
        "--json",
        action="store_true",
        help="Output the run summary in standardized JSON log format"
    )

    # 4. show-health subcommand
    health_parser = subparsers.add_parser("show-health", help="Report execution health status for all sources")
    health_parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        default=DEFAULT_DB_PATH,
        help="Custom SQLite canonical database path"
    )
    health_parser.add_argument(
        "--json",
        action="store_true",
        help="Output the source health report in structured JSON log format"
    )

    return parser

def cmd_validate(config_dir: pathlib.Path) -> int:
    """Validates configuration files and prints errors/warnings."""
    try:
        config = load_config(config_dir)
        errors, warnings = validate_config(config)
        
        if warnings:
            print("CONFIG WARNINGS:", file=sys.stderr)
            for w in warnings:
                print(f"  - WARNING: {w}", file=sys.stderr)
                
        if errors:
            print("CONFIG VALIDATION FAILED:", file=sys.stderr)
            for e in errors:
                print(f"  - ERROR: {e}", file=sys.stderr)
            return 1
            
        print("Configuration validated successfully (zero errors found).")
        return 0
    except Exception as e:
        print(f"Failed to load or parse configuration: {str(e)}", file=sys.stderr)
        return 1

def cmd_migrate(db_path: pathlib.Path) -> int:
    """Runs database migrations."""
    try:
        print(f"Running database migrations on database: {db_path}...")
        run_migrations(db_path, DEFAULT_MIGRATIONS_DIR)
        print("Database schema migrations executed successfully.")
        return 0
    except Exception as e:
        print(f"Migration failed: {str(e)}", file=sys.stderr)
        return 1

def cmd_show_health(config_dir: pathlib.Path, db_path: pathlib.Path, use_json: bool) -> int:
    """Queries and reports source health states."""
    try:
        config = load_config(config_dir)
    except Exception as e:
        print(f"Failed to load configuration: {str(e)}", file=sys.stderr)
        return 1

    # Open connection to read states
    conn = None
    states_dict = {}
    if db_path.exists():
        try:
            conn = get_connection(db_path)
            state_repo = SourceStateRepository(conn)
            for source in config.sources:
                row = state_repo.get(source.id)
                if row:
                    states_dict[source.id] = dict(row)
        except Exception as e:
            print(f"Failed to read database states: {str(e)}", file=sys.stderr)
            return 1
        finally:
            if conn:
                conn.close()

    # Compile report
    report = []
    for s in config.sources:
        state = states_dict.get(s.id) or {
            "health_status": "healthy",
            "consecutive_failures": 0,
            "last_error_class": None,
            "quarantine_until": None,
            "last_success_at": None
        }
        report.append({
            "source_id": s.id,
            "title": s.title,
            "health_status": state.get("health_status"),
            "consecutive_failures": state.get("consecutive_failures"),
            "last_error_class": state.get("last_error_class"),
            "quarantine_until": state.get("quarantine_until"),
            "last_success_at": state.get("last_success_at")
        })

    if use_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"{'ID':<6} | {'Title':<40} | {'Status':<12} | {'Failures':<8} | {'Quarantine Until':<20} | {'Last Success':<20}")
        print("-" * 118)
        for r in report:
            title_truncated = r["title"][:38] + ".." if len(r["title"]) > 40 else r["title"]
            print(f"{r['source_id']:<6} | {title_truncated:<40} | {r['health_status']:<12} | {r['consecutive_failures']:<8} | {str(r['quarantine_until'] or '-'):<20} | {str(r['last_success_at'] or '-'):<20}")

    return 0

def cmd_fetch(args: argparse.Namespace) -> int:
    """Executes the fetch pipeline orchestration."""
    # 1. Load config
    try:
        config = load_config(args.config_dir)
    except Exception as e:
        print(f"Failed to load configuration: {str(e)}", file=sys.stderr)
        return 1

    # 2. Validate config first (unless dry-run has another path, but standard is to fail-fast)
    errors, warnings = validate_config(config)
    if errors:
        print("Configuration validation failed before execution. Run 'validate' subcommand to see details.", file=sys.stderr)
        return 1

    # 3. Trigger migrations automatically on target database (if not dry_run)
    if not args.dry_run:
        try:
            run_migrations(args.db_path, DEFAULT_MIGRATIONS_DIR)
        except Exception as e:
            print(f"Auto-migration failed before execution: {str(e)}", file=sys.stderr)
            return 1

    # 4. Execute Orchestrator
    try:
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=args.db_path,
            trigger_type=args.trigger_type,
            groups=args.groups,
            source_ids=args.source_ids,
            force=args.force,
            dry_run=args.dry_run
        ))
    except Exception as e:
        print(f"Orchestration critical failure: {str(e)}", file=sys.stderr)
        return 1

    # 5. Output Summary
    if args.json:
        # Standardized JSON output
        summary_dict = {
            "fetch_run_id": summary.fetch_run_id,
            "started_at": summary.started_at,
            "ended_at": summary.ended_at,
            "run_scope": summary.run_scope,
            "trigger_type": summary.trigger_type,
            "run_status": summary.run_status,
            "due_source_count": summary.due_source_count,
            "attempted_source_count": summary.attempted_source_count,
            "succeeded_source_count": summary.succeeded_source_count,
            "failed_source_count": summary.failed_source_count,
            "new_item_count": summary.new_item_count,
            "dedup_matched_count": summary.dedup_matched_count,
            "quarantined_count": summary.quarantined_count,
            "skipped_reasons": summary.skipped_reasons,
            "error_summary": summary.error_summary
        }
        print(json.dumps(summary_dict, indent=2, ensure_ascii=False))
    else:
        # Beautiful human-readable text output
        print("==================================================================================")
        print(f"INGEST FETCH RUN COMPLETED: {summary.run_status.upper()}")
        print("==================================================================================")
        print(f"  Run ID:                {summary.fetch_run_id}")
        print(f"  Status:                {summary.run_status}")
        print(f"  Trigger:               {summary.trigger_type}")
        print(f"  Scope:                 {summary.run_scope}")
        print(f"  Started At:            {summary.started_at}")
        print(f"  Ended At:              {summary.ended_at}")
        print(f"  Due Source Count:      {summary.due_source_count}")
        print(f"  Attempted Count:       {summary.attempted_source_count}")
        print(f"  Succeeded Count:       {summary.succeeded_source_count}")
        print(f"  Failed Count:          {summary.failed_source_count}")
        print(f"  Newly Ingested Items:  {summary.new_item_count}")
        print(f"  Deduplicated Matches:  {summary.dedup_matched_count}")
        print(f"  Quarantined Sources:   {summary.quarantined_count}")
        if summary.skipped_reasons:
            print("  Skipped Reasons:")
            for reason, count in summary.skipped_reasons.items():
                print(f"    - {reason:<12}: {count}")
        if summary.error_summary:
            print("==================================================================================")
            print("ERROR SUMMARY:")
            print(summary.error_summary)
        print("==================================================================================")

    # Return exit code based on run success (partial_failure also succeeds the CLI run itself)
    if summary.run_status == "failed":
        return 1
    return 0

def main(argv: Optional[List[str]] = None) -> int:
    parser = get_args_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return cmd_validate(args.config_dir)
    elif args.command == "migrate":
        return cmd_migrate(args.db_path)
    elif args.command == "show-health":
        return cmd_show_health(args.config_dir, args.db_path, args.json)
    elif args.command == "fetch":
        return cmd_fetch(args)
    return 0

if __name__ == "__main__":
    sys.exit(main())
