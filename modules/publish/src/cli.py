import asyncio
import pathlib
import sys
from typing import List, Optional

import click

from .config import validate_and_load_config
from .database import run_migrations, get_connection, PublishRepository
from .orchestrator import orchestrate_run

DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
DEFAULT_CONFIG_PATH = DEFAULT_WORKSPACE_ROOT / "modules" / "publish" / "config" / "publish_settings.yaml"
DEFAULT_MIGRATIONS_DIR = DEFAULT_WORKSPACE_ROOT / "modules" / "publish" / "src" / "migrations"
SUMMARY_SEPARATOR = "=" * 82


def print_run_summary(summary: dict, cmd_name: str) -> None:
    click.echo(SUMMARY_SEPARATOR)
    click.echo(f"PUBLISH {cmd_name.upper()} SUMMARY: {summary['status'].upper()}")
    click.echo(SUMMARY_SEPARATOR)
    if summary["status"] == "success":
        click.echo(f"  Published/Updated: {summary['published_count']}")
        click.echo(f"  Withdrawn/Cleaned: {summary['withdrawn_count']}")
    else:
        click.echo(f"  Errors encountered: {', '.join(summary['errors'])}")
    click.echo(SUMMARY_SEPARATOR)


@click.group()
@click.option(
    "--config-path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=pathlib.Path),
    default=DEFAULT_CONFIG_PATH,
    help="Path to configuration YAML file"
)
@click.option(
    "--migrations-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=pathlib.Path),
    default=DEFAULT_MIGRATIONS_DIR,
    help="Path to SQL migrations directory"
)
@click.pass_context
def cli(ctx, config_path, migrations_dir):
    """Publish Module Command-Line Interface (UAP Aggregation System)"""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["migrations_dir"] = migrations_dir


@cli.command("validate")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
@click.pass_context
def cmd_validate(ctx, db_path):
    """Validate settings and target languages in database"""
    config_path = ctx.obj["config_path"]
    try:
        config = validate_and_load_config(config_path)
        
        # Check target language existence in database (blocking failure for validate command)
        if not db_path.exists():
            raise ValueError(f"Database file does not exist at {db_path}")

        conn = get_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='translation_output'")
            if not cursor.fetchone():
                raise ValueError("translation_output table does not exist in the database (run migrate first)")
            
            cursor.execute("SELECT DISTINCT language_code FROM translation_output WHERE translation_status = 'completed'")
            completed_langs = {row[0] for row in cursor.fetchall()}
            
            for lang in config.target_languages:
                if lang not in completed_langs:
                    raise ValueError(f"Configured target language '{lang}' has zero completed translations in the database.")
        finally:
            conn.close()

        click.echo("Configuration and system check validated successfully (zero errors found).")
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
    "--export-dir",
    type=click.Path(path_type=pathlib.Path),
    help="Override default export directory"
)
@click.pass_context
def cmd_run(ctx, db_path, export_dir):
    """Run incremental publish synchronization"""
    config_path = ctx.obj["config_path"]
    migrations_dir = ctx.obj["migrations_dir"]

    # Load configuration
    try:
        config = validate_and_load_config(config_path)
    except Exception as e:
        click.echo(f"Configuration validation failed: {e}", err=True)
        sys.exit(1)

    # Set export dir
    if export_dir is None:
        export_dir = DEFAULT_WORKSPACE_ROOT / config.execution_policy.default_export_dir

    # Auto-run migrations
    try:
        run_migrations(db_path, migrations_dir)
    except Exception as e:
        click.echo(f"Auto-migration failed: {str(e)}", err=True)
        sys.exit(1)

    # Run orchestrator
    try:
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=db_path,
            export_dir=export_dir,
            rebuild=False
        ))
    except Exception as e:
        click.echo(f"Orchestration critical failure: {str(e)}", err=True)
        sys.exit(1)

    print_run_summary(summary, "run")
    if summary["status"] == "failure":
        sys.exit(1)


@cli.command("rebuild")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
@click.option(
    "--export-dir",
    type=click.Path(path_type=pathlib.Path),
    help="Override default export directory"
)
@click.pass_context
def cmd_rebuild(ctx, db_path, export_dir):
    """Run a full rebuild of public static artifacts"""
    config_path = ctx.obj["config_path"]
    migrations_dir = ctx.obj["migrations_dir"]

    # Load configuration
    try:
        config = validate_and_load_config(config_path)
    except Exception as e:
        click.echo(f"Configuration validation failed: {e}", err=True)
        sys.exit(1)

    # Set export dir
    if export_dir is None:
        export_dir = DEFAULT_WORKSPACE_ROOT / config.execution_policy.default_export_dir

    # Auto-run migrations
    try:
        run_migrations(db_path, migrations_dir)
    except Exception as e:
        click.echo(f"Auto-migration failed: {str(e)}", err=True)
        sys.exit(1)

    # Run orchestrator in rebuild mode
    try:
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=db_path,
            export_dir=export_dir,
            rebuild=True
        ))
    except Exception as e:
        click.echo(f"Orchestration critical failure: {str(e)}", err=True)
        sys.exit(1)

    print_run_summary(summary, "rebuild")
    if summary["status"] == "failure":
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
    """Print publish-layer summary counts and status stats"""
    config_path = ctx.obj["config_path"]

    if not db_path.exists():
        click.echo(f"Database file does not exist: {db_path}", err=True)
        sys.exit(1)

    # Load config
    try:
        config = validate_and_load_config(config_path)
    except Exception as e:
        click.echo(f"Configuration validation failed: {e}", err=True)
        sys.exit(1)

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        
        # Verify table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publish_record'")
        if not cursor.fetchone():
            click.echo("Publish database tables do not exist yet. Run 'migrate' command.")
            sys.exit(0)

        # Count active published language artifacts
        cursor.execute("SELECT COUNT(*) FROM publish_language_status WHERE publish_status = 'published'")
        active_pub_count = cursor.fetchone()[0]

        # Count withdrawn language artifacts
        cursor.execute("SELECT COUNT(*) FROM publish_language_status WHERE publish_status = 'withdrawn'")
        withdrawn_count = cursor.fetchone()[0]

        # Count total source items with frozen slugs
        cursor.execute("SELECT COUNT(*) FROM publish_record")
        total_frozen_slugs = cursor.fetchone()[0]

        # Count items eligible & blocked under the active coverage policy (Issue 5 fix)
        # Fetch all approved item IDs from curation_decision
        cursor.execute("SELECT source_item_id FROM curation_decision WHERE curate_status = 'approved'")
        approved_ids = {row[0] for row in cursor.fetchall()}

        # Fetch completed, matching translation language codes per source item
        cursor.execute("""
            SELECT a.source_item_id, t.language_code
            FROM approved_content_record a
            JOIN translation_output t ON t.parent_content_id = a.parent_content_id AND t.source_fingerprint = a.content_fingerprint
            WHERE t.translation_status = 'completed'
        """)
        completed_langs = {}
        for row in cursor.fetchall():
            item_id, lang = row[0], row[1]
            if item_id not in completed_langs:
                completed_langs[item_id] = set()
            completed_langs[item_id].add(lang)

        configured_langs = set(config.target_languages.keys())
        eligible_count = 0
        blocked_count = 0
        
        for item_id in approved_ids:
            langs = completed_langs.get(item_id, set())
            if configured_langs.issubset(langs):
                eligible_count += 1
            else:
                blocked_count += 1


        click.echo(SUMMARY_SEPARATOR)
        click.echo("PUBLISH STATE PROJECT STATUS SUMMARY")
        click.echo(SUMMARY_SEPARATOR)
        click.echo(f"  Active Published Artifacts:  {active_pub_count}")
        click.echo(f"  Withdrawn Artifacts:         {withdrawn_count}")
        click.echo(f"  Total Items with Frozen Slugs: {total_frozen_slugs}")
        click.echo(f"  Eligible Source Items:       {eligible_count}")
        click.echo(f"  Blocked Source Items:        {blocked_count}")
        click.echo(SUMMARY_SEPARATOR)

    except Exception as e:
        click.echo(f"Error querying publish status: {str(e)}", err=True)
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
