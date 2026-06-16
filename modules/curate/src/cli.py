import asyncio
import pathlib
import sys
from typing import List, Optional

import click
from dotenv import load_dotenv

from .config import validate_and_load_config
from .database import run_migrations, get_connection
from .orchestrator import orchestrate_run

DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
DEFAULT_CONFIG_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "curate" / "config"
DEFAULT_MIGRATIONS_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "curate" / "src" / "migrations"
SUMMARY_SEPARATOR = "=" * 82


def print_run_summary(summary: dict) -> None:
    click.echo(SUMMARY_SEPARATOR)
    click.echo(f"CURATE BATCH RUN SUMMARY: {summary['status'].upper()}")
    click.echo(SUMMARY_SEPARATOR)
    click.echo(f"  Total Queried:             {summary['total_queried']}")
    if summary["status"] == "preview":
        click.echo(f"  Previewed Prompts:         {summary['previewed']}")
    else:
        click.echo(f"  Processed Successfully:    {summary['processed_successfully']}")
        click.echo(f"  Failures:                  {summary['failures']}")
    click.echo(SUMMARY_SEPARATOR)


@click.group()
@click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=pathlib.Path),
    default=DEFAULT_CONFIG_DIR,
    help="Path to configuration directory containing model_settings.yaml and prompt_templates.yaml"
)
@click.option(
    "--migrations-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=pathlib.Path),
    default=DEFAULT_MIGRATIONS_DIR,
    help="Path to curate SQL migrations directory containing DDL scripts"
)
@click.pass_context
def cli(ctx, config_dir, migrations_dir):
    """Curate Module Command-Line Interface (UAP Aggregation System)"""
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config_dir
    ctx.obj["migrations_dir"] = migrations_dir
    # Load .env variables
    load_dotenv(dotenv_path=DEFAULT_WORKSPACE_ROOT / ".env", override=False)


@cli.command("validate")
@click.pass_context
def cmd_validate(ctx):
    """Validate model settings and prompt configurations"""
    config_dir = ctx.obj["config_dir"]
    try:
        validate_and_load_config(config_dir)
        click.echo("Configuration validated successfully (zero errors found).")
    except Exception as e:
        click.echo(f"CONFIG VALIDATION FAILED: {e}", err=True)
        sys.exit(1)


@cli.command("migrate")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
@click.pass_context
def cmd_migrate(ctx, db_path):
    """Execute SQLite database schema migrations"""
    migrations_dir = ctx.obj["migrations_dir"]
    try:
        click.echo(f"Running database migrations on database: {db_path}...")
        run_migrations(db_path, migrations_dir)
        click.echo("Database schema migrations executed successfully.")
    except Exception as e:
        click.echo(f"Migration failed: {str(e)}", err=True)
        sys.exit(1)


@cli.command("run")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
@click.option(
    "--batch-size",
    type=int,
    help="Override batch size config"
)
@click.option(
    "--preview-prompts",
    is_flag=True,
    help="Print prompt payloads for pending items without calling the API or writing to DB"
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Run complete curation including LLM API requests, but do not commit DB writes"
)
@click.option(
    "--source-item-id",
    type=int,
    help="Curate a specific item (can be a completed item if --force is also specified)"
)
@click.option(
    "--force",
    is_flag=True,
    help="Allow re-curation of completed (approved/rejected) items when curating a specific item"
)
@click.pass_context
def cmd_run(ctx, db_path, batch_size, preview_prompts, dry_run, source_item_id, force):
    """Run the curation pipeline on pending items"""
    config_dir = ctx.obj["config_dir"]
    migrations_dir = ctx.obj["migrations_dir"]

    if force and source_item_id is None:
        raise click.UsageError("--force can only be used when curating a specific item using --source-item-id.")

    # Load settings & templates
    try:
        config = validate_and_load_config(config_dir)
    except Exception as e:
        click.echo(f"Configuration validation failed: {e}", err=True)
        sys.exit(1)

    # Always run migrations before execution (even preview-prompts)
    try:
        run_migrations(db_path, migrations_dir)
    except Exception as e:
        click.echo(f"Auto-migration failed before execution: {str(e)}", err=True)
        sys.exit(1)

    # Execute orchestrator run
    try:
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=db_path,
            batch_size=batch_size,
            preview_prompts=preview_prompts,
            dry_run=dry_run,
            source_item_id=source_item_id,
            force=force
        ))
    except Exception as e:
        click.echo(f"Orchestrator critical failure: {str(e)}", err=True)
        sys.exit(1)

    # Print summary output
    print_run_summary(summary)

    if summary.get("failures", 0) > 0 and summary.get("processed_successfully", 0) == 0:
        sys.exit(1)


@cli.command("status")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
def cmd_status(db_path):
    """Print curation queue stats and status counts"""
    if not db_path.exists():
        click.echo(f"Database file does not exist: {db_path}", err=True)
        sys.exit(1)

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()

        # 1. pending count
        cursor.execute("""
            SELECT COUNT(*) FROM source_item s
            JOIN classification_result c ON s.source_item_id = c.source_item_id
            LEFT JOIN curation_decision r ON s.source_item_id = r.source_item_id
            WHERE s.ingest_status = 'ingested'
              AND c.topic_class IN ('core', 'adjacent')
              AND (r.curation_decision_id IS NULL OR (r.curate_status = 'failed' AND r.retry_count < 3))
        """)
        pending_count = cursor.fetchone()[0]

        # 2. locked count (failed permanently)
        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision
            WHERE curate_status = 'failed' AND retry_count >= 3
        """)
        locked_count = cursor.fetchone()[0]

        # 3. approved counts (total, publish_link, publish_summary)
        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision WHERE curate_status = 'approved'
        """)
        approved_total = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision 
            WHERE curate_status = 'approved' AND downstream_action = 'publish_link'
        """)
        approved_link = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision 
            WHERE curate_status = 'approved' AND downstream_action = 'publish_summary'
        """)
        approved_summary = cursor.fetchone()[0]

        # 4. rejected counts (total, edit_rewrite, reject_discard)
        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision WHERE curate_status = 'rejected'
        """)
        rejected_total = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision 
            WHERE curate_status = 'rejected' AND downstream_action = 'edit_rewrite'
        """)
        rejected_rewrite = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision 
            WHERE curate_status = 'rejected' AND downstream_action = 'reject_discard'
        """)
        rejected_discard = cursor.fetchone()[0]

        # 5. total_failed_runs
        cursor.execute("""
            SELECT COUNT(*) FROM curation_decision WHERE curate_status = 'failed'
        """)
        total_failed_runs = cursor.fetchone()[0]

        click.echo(SUMMARY_SEPARATOR)
        click.echo("CURATION QUEUE STATUS SUMMARY")
        click.echo(SUMMARY_SEPARATOR)
        click.echo(f"  pending:                  {pending_count}")
        click.echo(f"  locked (failed permanent):{locked_count}")
        click.echo(f"  approved:                 {approved_total}")
        click.echo(f"    - publish_link:         {approved_link}")
        click.echo(f"    - publish_summary:      {approved_summary}")
        click.echo(f"  rejected:                 {rejected_total}")
        click.echo(f"    - edit_rewrite:         {rejected_rewrite}")
        click.echo(f"    - reject_discard:       {rejected_discard}")
        click.echo(f"  total_failed_runs:        {total_failed_runs}")
        click.echo(SUMMARY_SEPARATOR)

    except Exception as e:
        click.echo(f"Error querying curation status: {str(e)}", err=True)
        sys.exit(1)
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    try:
        cli(args=argv, standalone_mode=False)
        return 0
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except SystemExit as e:
        return e.code
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
