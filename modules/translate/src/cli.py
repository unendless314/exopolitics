import asyncio
import pathlib
import sys
from typing import List, Optional

import click
from dotenv import load_dotenv

from .config import validate_and_load_config
from .database import run_migrations, get_connection
from .orchestrator import orchestrate_run
from .approved_content_record import assemble_approved_content_records

DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
DEFAULT_CONFIG_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "translate" / "config"
DEFAULT_MIGRATIONS_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "translate" / "src" / "migrations"
SUMMARY_SEPARATOR = "=" * 82


def print_run_summary(summary: dict) -> None:
    click.echo(SUMMARY_SEPARATOR)
    click.echo(f"TRANSLATE BATCH RUN SUMMARY: {summary['status'].upper()}")
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
    help="Path to translate SQL migrations directory containing DDL scripts"
)
@click.pass_context
def cli(ctx, config_dir, migrations_dir):
    """Translate Module Command-Line Interface (UAP Aggregation System)"""
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


@cli.command("assemble")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
@click.pass_context
def cmd_assemble(ctx, db_path):
    """Run the co-located shared handoff assembler to sync curation/edits to approved_content_record"""
    # Run database migration to ensure tables exist
    migrations_dir = ctx.obj["migrations_dir"]
    try:
        run_migrations(db_path, migrations_dir)
    except Exception as e:
        click.echo(f"Auto-migration failed: {str(e)}", err=True)
        sys.exit(1)

    try:
        click.echo(f"Running shared handoff assembler on database: {db_path}...")
        conn = get_connection(db_path)
        try:
            stats = assemble_approved_content_records(conn)
            click.echo(SUMMARY_SEPARATOR)
            click.echo("HANDOFF ASSEMBLY COMPLETED")
            click.echo(SUMMARY_SEPARATOR)
            click.echo(f"  Scanned Upstream: {stats['scanned']}")
            click.echo(f"  Inserted Records: {stats['inserted']}")
            click.echo(f"  Updated Records:  {stats['updated']}")
            click.echo(f"  Skipped (Delta):  {stats['skipped']}")
            click.echo(SUMMARY_SEPARATOR)
        finally:
            conn.close()
    except Exception as e:
        click.echo(f"Assembly failed: {str(e)}", err=True)
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
    help="Run complete translation including LLM API requests, but do not commit DB writes"
)
@click.option(
    "--parent-content-id",
    type=int,
    help="Translate a specific approved content mother draft"
)
@click.option(
    "--language-code",
    type=str,
    help="Target language code when translating a specific approved content mother draft"
)
@click.option(
    "--force",
    is_flag=True,
    help="Allow re-translation of completed items when translating a specific approved content mother draft"
)
@click.option(
    "--assemble",
    "run_assemble",
    is_flag=True,
    help="Run the shared handoff assembler automatically before translating"
)
@click.pass_context
def cmd_run(ctx, db_path, batch_size, preview_prompts, dry_run, parent_content_id, language_code, force, run_assemble):
    """Run the translation pipeline on pending items"""
    config_dir = ctx.obj["config_dir"]
    migrations_dir = ctx.obj["migrations_dir"]

    if force and parent_content_id is None:
        raise click.UsageError("--force can only be used when translating a specific item using --parent-content-id.")

    # Load settings & templates
    try:
        config = validate_and_load_config(config_dir)
    except Exception as e:
        click.echo(f"Configuration validation failed: {e}", err=True)
        sys.exit(1)

    # Always run migrations before execution
    try:
        run_migrations(db_path, migrations_dir)
    except Exception as e:
        click.echo(f"Auto-migration failed before execution: {str(e)}", err=True)
        sys.exit(1)

    # Optionally run assembly first
    if run_assemble and not preview_prompts and not dry_run:
        try:
            click.echo("Auto-running handoff assembler...")
            conn = get_connection(db_path)
            try:
                assemble_approved_content_records(conn)
            finally:
                conn.close()
        except Exception as e:
            click.echo(f"Handoff assembler failed: {e}", err=True)
            sys.exit(1)

    # Execute orchestrator run
    try:
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=db_path,
            batch_size=batch_size,
            preview_prompts=preview_prompts,
            dry_run=dry_run,
            parent_content_id=parent_content_id,
            language_code=language_code,
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
@click.pass_context
def cmd_status(ctx, db_path):
    """Print translation queue stats and status counts by language"""
    config_dir = ctx.obj["config_dir"]

    if not db_path.exists():
        click.echo(f"Database file does not exist: {db_path}", err=True)
        sys.exit(1)

    # Load settings
    try:
        config = validate_and_load_config(config_dir)
    except Exception as e:
        click.echo(f"Configuration validation failed: {e}", err=True)
        sys.exit(1)

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        
        # Verify table exists (if not, count is 0)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='approved_content_record'")
        if not cursor.fetchone():
            click.echo("Translate database tables do not exist yet. Run 'migrate' command.")
            sys.exit(0)

        target_langs = list(config.target_languages.keys())
        retry_attempts = config.execution_policy.retry_attempts

        click.echo(SUMMARY_SEPARATOR)
        click.echo("TRANSLATE QUEUE STATUS SUMMARY")
        click.echo(SUMMARY_SEPARATOR)

        for lang in target_langs:
            # 1. Completed
            cursor.execute("""
                SELECT COUNT(*) FROM translation_output 
                WHERE language_code = ? AND translation_status = 'completed'
            """, (lang,))
            completed = cursor.fetchone()[0]

            # 2. Stale
            cursor.execute("""
                SELECT COUNT(*) FROM translation_output 
                WHERE language_code = ? AND translation_status = 'stale'
            """, (lang,))
            stale = cursor.fetchone()[0]

            # 3. Failed
            cursor.execute("""
                SELECT COUNT(*) FROM translation_output 
                WHERE language_code = ? AND translation_status = 'failed' AND retry_count < ?
            """, (lang, retry_attempts))
            failed = cursor.fetchone()[0]

            # 4. Locked
            cursor.execute("""
                SELECT COUNT(*) FROM translation_output 
                WHERE language_code = ? AND translation_status = 'failed' AND retry_count >= ?
            """, (lang, retry_attempts))
            locked = cursor.fetchone()[0]

            # 5. Total approved content records
            cursor.execute("SELECT COUNT(*) FROM approved_content_record")
            total_records = cursor.fetchone()[0]

            # How many rows exist in translation_output for this lang?
            cursor.execute("SELECT COUNT(*) FROM translation_output WHERE language_code = ?", (lang,))
            existing_count = cursor.fetchone()[0]

            no_row_count = total_records - existing_count
            if no_row_count < 0:
                no_row_count = 0

            cursor.execute("""
                SELECT COUNT(*) FROM translation_output 
                WHERE language_code = ? AND translation_status = 'pending'
            """, (lang,))
            pending_status = cursor.fetchone()[0]

            # Total eligible pending tasks
            pending = no_row_count + pending_status + stale + failed

            click.echo(f"Language: {lang.upper()} ({config.target_languages[lang].label})")
            click.echo(f"  completed:                 {completed}")
            click.echo(f"  pending (eligible total):  {pending}")
            click.echo(f"    - no translation:        {no_row_count}")
            click.echo(f"    - pending status:        {pending_status}")
            click.echo(f"    - stale status:          {stale}")
            click.echo(f"    - failed (retryable):    {failed}")
            click.echo(f"  locked (failed permanent): {locked}")
            click.echo(SUMMARY_SEPARATOR)

    except Exception as e:
        click.echo(f"Error querying translation status: {str(e)}", err=True)
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
