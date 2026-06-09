import argparse
import asyncio
import pathlib
import sys
from typing import List, Optional

from .config import validate_and_load_config
from .database import run_migrations
from .orchestrator import orchestrate_run

DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
DEFAULT_CONFIG_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "ingest" / "config"
DEFAULT_MIGRATIONS_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "ingest" / "src" / "migrations"

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
        help="Bypass schedule interval due verification and active quarantines"
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


    return parser

def cmd_validate(config_dir: pathlib.Path) -> int:
    """Validates configuration files and prints errors/warnings."""
    config, errors, warnings = validate_and_load_config(config_dir)
    
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

def cmd_fetch(args: argparse.Namespace) -> int:
    """Executes the fetch pipeline orchestration."""
    # Load and validate config
    config, errors, warnings = validate_and_load_config(args.config_dir)
    if errors:
        print("Configuration validation failed before execution. Run 'validate' subcommand to see details.", file=sys.stderr)
        for e in errors:
            print(f"  - ERROR: {e}", file=sys.stderr)
        return 1

    # Run auto-migrations if not dry-run
    if not args.dry_run:
        try:
            run_migrations(args.db_path, DEFAULT_MIGRATIONS_DIR)
        except Exception as e:
            print(f"Auto-migration failed before execution: {str(e)}", file=sys.stderr)
            return 1

    # Run orchestrator
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

    # Output results
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
    elif args.command == "fetch":
        return cmd_fetch(args)
    return 0

if __name__ == "__main__":
    sys.exit(main())
