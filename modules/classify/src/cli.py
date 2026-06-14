import argparse
import asyncio
import pathlib
import sys
from typing import List, Optional

from dotenv import load_dotenv

from .config import validate_and_load_config
from .database import run_migrations
from .orchestrator import orchestrate_run

DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
DEFAULT_CONFIG_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "classify" / "config"
DEFAULT_MIGRATIONS_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "classify" / "src" / "migrations"
SUMMARY_SEPARATOR = "=" * 82


def print_run_summary(summary: dict) -> None:
    print(SUMMARY_SEPARATOR)
    print(f"CLASSIFY BATCH RUN SUMMARY: {summary['status'].upper()}")
    print(SUMMARY_SEPARATOR)
    print(f"  Total Queried:             {summary['total_queried']}")
    if summary["status"] == "preview":
        print(f"  Previewed Prompts:         {summary['previewed']}")
    else:
        print(f"  Processed Successfully:    {summary['processed_successfully']}")
        print(f"  Failures:                  {summary['failures']}")
    print(SUMMARY_SEPARATOR)

def get_args_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify Module Command-Line Interface (UAP Aggregation System)"
    )
    parser.add_argument(
        "--config-dir",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_DIR,
        help="Path to configuration directory containing model_settings.yaml and prompt_templates.yaml"
    )
    parser.add_argument(
        "--migrations-dir",
        type=pathlib.Path,
        default=DEFAULT_MIGRATIONS_DIR,
        help="Path to classify SQL migrations directory containing DDL scripts"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")

    # 1. validate subcommand
    subparsers.add_parser("validate", help="Validate model settings and prompt configurations")

    # 2. migrate subcommand
    migrate_parser = subparsers.add_parser("migrate", help="Execute SQLite database schema migrations")
    migrate_parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        default=DEFAULT_DB_PATH,
        help="Custom SQLite canonical database path"
    )

    # 3. run subcommand
    run_parser = subparsers.add_parser("run", help="Run the classification pipeline on pending items")
    run_parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        default=DEFAULT_DB_PATH,
        help="Custom SQLite canonical database path"
    )
    run_parser.add_argument(
        "--batch-size",
        type=int,
        help="Override batch size config (number of unclassified items to fetch)"
    )
    run_parser.add_argument(
        "--preview-prompts",
        action="store_true",
        help="Print prompt payloads for pending items without calling the API or writing to DB"
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run complete classification including LLM API requests, but do not commit DB writes"
    )

    return parser

def cmd_validate(config_dir: pathlib.Path) -> int:
    """Validates configuration files and prints result."""
    try:
        validate_and_load_config(config_dir)
        print("Configuration validated successfully (zero errors found).")
        return 0
    except Exception as e:
        print(f"CONFIG VALIDATION FAILED: {e}", file=sys.stderr)
        return 1

def cmd_migrate(db_path: pathlib.Path, migrations_dir: pathlib.Path) -> int:
    """Runs database migrations for classification tables."""
    try:
        print(f"Running database migrations on database: {db_path}...")
        run_migrations(db_path, migrations_dir)
        print("Database schema migrations executed successfully.")
        return 0
    except Exception as e:
        print(f"Migration failed: {str(e)}", file=sys.stderr)
        return 1

def cmd_run(args: argparse.Namespace) -> int:
    """Executes the classification pipeline run."""
    # Load settings & templates
    try:
        config = validate_and_load_config(args.config_dir)
    except Exception as e:
        print(f"Configuration validation failed: {e}", file=sys.stderr)
        return 1

    # Auto-run migrations if running (and not dry-run or preview)
    if not args.preview_prompts:
        try:
            run_migrations(args.db_path, args.migrations_dir)
        except Exception as e:
            print(f"Auto-migration failed before execution: {str(e)}", file=sys.stderr)
            return 1

    # Execute orchestrator run
    try:
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=args.db_path,
            batch_size=args.batch_size,
            preview_prompts=args.preview_prompts,
            dry_run=args.dry_run
        ))
    except Exception as e:
        print(f"Orchestrator critical failure: {str(e)}", file=sys.stderr)
        return 1

    # Print summary output
    print_run_summary(summary)

    if summary.get("failures", 0) > 0 and summary.get("processed_successfully", 0) == 0:
        # If everything queried failed, exit with error status code
        return 1
    return 0

def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass
    
    # Load .env variables from workspace root for local development/execution
    load_dotenv(dotenv_path=DEFAULT_WORKSPACE_ROOT / ".env", override=False)
    
    parser = get_args_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        return cmd_validate(args.config_dir)
    elif args.command == "migrate":
        return cmd_migrate(args.db_path, args.migrations_dir)
    elif args.command == "run":
        return cmd_run(args)
    return 0

if __name__ == "__main__":
    sys.exit(main())
