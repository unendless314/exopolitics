import argparse
import asyncio
import html
import json
import os
import pathlib
import sys
from typing import List, Optional

from .config import load_classify_config
from .prompt_loader import load_prompt_templates
from .repository import run_migrations, get_connection, ClassificationRepository, get_utc_now_iso8601
from .classifier import classify_batch

DEFAULT_WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = DEFAULT_WORKSPACE_ROOT / "data" / "canonical.db"
DEFAULT_CONFIG_DIR = pathlib.Path(__file__).resolve().parent.parent / "config"
DEFAULT_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / "migrations"

def get_args_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify Module Command-Line Interface (UAP Aggregation System)"
    )
    parser.add_argument(
        "--config-dir",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_DIR,
        help="Path to classify configuration directory containing model_settings.yaml and prompt_templates.yaml"
    )
    parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        default=DEFAULT_DB_PATH,
        help="Custom SQLite canonical database path"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")

    # 1. migrate subcommand
    subparsers.add_parser("migrate", help="Execute SQLite database schema migrations for classify module")

    # 2. run subcommand
    subparsers.add_parser("run", help="Run LLM classification on pending source items")

    # 3. export-report subcommand
    report_parser = subparsers.add_parser("export-report", help="Export a diagnostic report of the classification results")
    report_parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=DEFAULT_WORKSPACE_ROOT / "data" / "classify_report.html",
        help="Path where the HTML report should be saved"
    )

    return parser

def cmd_migrate(db_path: pathlib.Path) -> int:
    """Executes database migrations for the classify module."""
    try:
        conn = get_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_item'")
            if not cursor.fetchone():
                print("Error: Required upstream table 'source_item' is missing. Please run ingest migrations first.", file=sys.stderr)
                return 1
        finally:
            conn.close()

        print(f"Running database migrations on database: {db_path}...", file=sys.stderr)
        run_migrations(db_path, DEFAULT_MIGRATIONS_DIR)
        print("Migrations applied successfully.", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Failed to run migrations: {str(e)}", file=sys.stderr)
        return 1

def cmd_run(config_dir: pathlib.Path, db_path: pathlib.Path) -> int:
    """Runs the classification pipeline on pending items."""
    # 1. Load config and templates
    try:
        config = load_classify_config(config_dir / "model_settings.yaml")
        templates = load_prompt_templates(config_dir / "prompt_templates.yaml")
    except Exception as e:
        print(f"Failed to load configurations: {str(e)}", file=sys.stderr)
        return 1

    # Get active prompt template
    template_name = config.active_prompt_template
    template = templates.get(template_name)
    if not template:
        print(f"Active template '{template_name}' not found in prompt_templates.yaml", file=sys.stderr)
        return 1

    # 2. Check dependencies and auto-run migrations
    try:
        conn = get_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_item'")
            if not cursor.fetchone():
                print("Error: Required upstream table 'source_item' is missing. Please run ingest migrations first.", file=sys.stderr)
                return 1
        finally:
            conn.close()

        run_migrations(db_path, DEFAULT_MIGRATIONS_DIR)
    except Exception as e:
        print(f"Auto-migration failed: {str(e)}", file=sys.stderr)
        return 1

    # 3. Get connection and repository
    conn = get_connection(db_path)
    repo = ClassificationRepository(conn)
    
    try:
        # Fetch pending items
        batch_size = config.execution_policy.batch_size
        pending = repo.get_pending_items(batch_size)
        
        if not pending:
            print("No pending items found to classify.", file=sys.stderr)
            return 0
            
        print(f"Found {len(pending)} pending items to classify. Starting orchestrator...", file=sys.stderr)
        
        # Setup counters for reporting
        stats = {"success": 0, "low-context": 0, "failed": 0}
        
        def progress_callback(source_item_id: int, outcome: str, topic_class: Optional[str]):
            stats[outcome] = stats.get(outcome, 0) + 1
            if outcome == "low-context":
                print(f"Item {source_item_id:04d}: [LOW-CONTEXT] -> classified as unknown", file=sys.stderr)
            elif outcome == "success":
                print(f"Item {source_item_id:04d}: [LLM] -> classified as {topic_class}", file=sys.stderr)
            else:
                print(f"Item {source_item_id:04d}: [FAILED]", file=sys.stderr)

        # Run async batch execution
        results = asyncio.run(
            classify_batch(
                items=pending,
                config=config,
                template=template,
                repo=repo,
                progress_callback=progress_callback
            )
        )
        


        # Output Summary Statistics
        print("==================================================================================", file=sys.stderr)
        print("CLASSIFICATION RUN COMPLETED", file=sys.stderr)
        print("==================================================================================", file=sys.stderr)
        print(f"  Total Pending Found:   {len(pending)}", file=sys.stderr)
        print(f"  LLM Successes:         {stats['success']}", file=sys.stderr)
        print(f"  Low-Context Skipped:   {stats['low-context']}", file=sys.stderr)
        print(f"  Failed / Unclassified: {len(pending) - len(results)}", file=sys.stderr)
        print("==================================================================================", file=sys.stderr)

    except Exception as e:
        print(f"Classifier execution failure: {str(e)}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0

def cmd_export_report(db_path: pathlib.Path, out_path: pathlib.Path) -> int:
    """Generates a premium diagnostic HTML report from database classification results."""
    if not db_path.exists():
        print(f"Database file does not exist at {db_path}", file=sys.stderr)
        return 1

    try:
        conn = get_connection(db_path)
        cursor = conn.cursor()
        
        # Fetch stats
        cursor.execute("SELECT COUNT(*) FROM classification_result")
        total_classified = cursor.fetchone()[0]

        cursor.execute("SELECT topic_class, COUNT(*) FROM classification_result GROUP BY topic_class")
        breakdown = {r[0]: r[1] for r in cursor.fetchall()}

        cursor.execute("""
            SELECT AVG(classification_confidence) 
            FROM classification_result 
            WHERE classification_confidence IS NOT NULL
        """)
        avg_confidence = cursor.fetchone()[0] or 0.0

        cursor.execute("SELECT COUNT(*) FROM classification_result WHERE edit_candidate = 1")
        edit_candidates = cursor.fetchone()[0]

        # Fetch latest 100 classified items with titles/summaries
        cursor.execute("""
            SELECT s.source_item_id, s.title, s.summary, 
                   c.topic_class, c.classification_reason, c.classification_confidence, c.edit_candidate, c.model_name
            FROM classification_result c
            JOIN source_item s ON c.source_item_id = s.source_item_id
            ORDER BY c.classified_at DESC
            LIMIT 100
        """)
        items = [dict(row) for row in cursor.fetchall()]
        
        conn.close()

        # Build premium styled HTML report
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        html_content = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UAP Classification Diagnostic Report</title>
    <style>
        :root {{
            --bg-color: #0d0f12;
            --surface-color: #161a22;
            --surface-accent: #212631;
            --text-color: #e6edf3;
            --text-muted: #8d96a0;
            --primary: #3fb950;
            --core-color: #ff7b72;
            --adjacent-color: #a5d6ff;
            --irrelevant-color: #8b949e;
            --unknown-color: #d2a8ff;
            --border-color: #30363d;
        }}
        body {{
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 24px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        header {{
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        h1 {{
            margin: 0 0 8px 0;
            font-size: 2rem;
            color: #ffffff;
            font-weight: 600;
        }}
        .subtitle {{
            color: var(--text-muted);
            margin: 0;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }}
        .card {{
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }}
        .card-title {{
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
        }}
        .card-value {{
            font-size: 1.8rem;
            font-weight: bold;
            color: #ffffff;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .badge-core {{ background-color: rgba(255, 123, 114, 0.15); color: var(--core-color); border: 1px solid rgba(255, 123, 114, 0.3); }}
        .badge-adjacent {{ background-color: rgba(165, 214, 255, 0.15); color: var(--adjacent-color); border: 1px solid rgba(165, 214, 255, 0.3); }}
        .badge-irrelevant {{ background-color: rgba(139, 148, 158, 0.15); color: var(--irrelevant-color); border: 1px solid rgba(139, 148, 158, 0.3); }}
        .badge-unknown {{ background-color: rgba(210, 168, 255, 0.15); color: var(--unknown-color); border: 1px solid rgba(210, 168, 255, 0.3); }}
        .table-container {{
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        th {{
            background-color: var(--surface-accent);
            color: var(--text-color);
            padding: 12px 16px;
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.85rem;
        }}
        td {{
            padding: 16px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: top;
            font-size: 0.9rem;
        }}
        tr:hover {{
            background-color: var(--surface-accent);
        }}
        .item-title {{
            font-weight: 600;
            color: #ffffff;
            margin-bottom: 4px;
        }}
        .item-summary {{
            color: var(--text-muted);
            font-size: 0.85rem;
            margin: 4px 0 0 0;
            max-height: 4.5em;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
        }}
        .reason-text {{
            font-size: 0.85rem;
            color: var(--text-color);
        }}
        .model-badge {{
            color: var(--text-muted);
            font-family: monospace;
            font-size: 0.75rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>UAP Classification Diagnostic Report</h1>
            <p class="subtitle">Generated on {get_utc_now_iso8601()}</p>
        </header>
        
        <div class="grid">
            <div class="card">
                <div class="card-title">Total Classified</div>
                <div class="card-value">{total_classified}</div>
            </div>
            <div class="card">
                <div class="card-title">Core / Adjacent</div>
                <div class="card-value">{breakdown.get('core', 0)} / {breakdown.get('adjacent', 0)}</div>
            </div>
            <div class="card">
                <div class="card-title">Avg Confidence</div>
                <div class="card-value">{avg_confidence:.2%}</div>
            </div>
            <div class="card">
                <div class="card-title">Edit Candidates</div>
                <div class="card-value">{edit_candidates}</div>
            </div>
        </div>

        <h2>Latest Classification Results (Up to 100)</h2>
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="width: 80px;">Item ID</th>
                        <th style="width: 120px;">Topic Class</th>
                        <th>Feed Metadata</th>
                        <th>Reasoning & Confidence</th>
                        <th style="width: 150px;">Metadata</th>
                    </tr>
                </thead>
                <tbody>
        """
        for item in items:
            tc = item["topic_class"]
            badge_class = f"badge-{html.escape(tc)}"
            conf_str = f"{item['classification_confidence']:.2%}" if item['classification_confidence'] is not None else "N/A"
            edit_marker = "⭐ Yes" if item['edit_candidate'] == 1 else "No"
            
            html_content += f"""
                    <tr>
                        <td>{item['source_item_id']}</td>
                        <td><span class="badge {badge_class}">{html.escape(tc)}</span></td>
                        <td>
                            <div class="item-title">{html.escape(item['title'])}</div>
                            <p class="item-summary">{html.escape(item['summary'] or '')}</p>
                        </td>
                        <td>
                            <div class="reason-text">{html.escape(item['classification_reason'] or '')}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">
                                Confidence: {conf_str}
                            </div>
                        </td>
                        <td>
                            <div class="model-badge">Model: {html.escape(item['model_name'])}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 4px;">
                                Edit Candidate: {edit_marker}
                            </div>
                        </td>
                    </tr>
            """
            
        html_content += """
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""
        out_path.write_text(html_content, encoding="utf-8")
        print(f"HTML classification diagnostic report generated successfully at: {out_path}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Failed to generate classification report: {str(e)}", file=sys.stderr)
        return 1

def main(argv: Optional[List[str]] = None) -> int:
    parser = get_args_parser()
    args = parser.parse_args(argv)

    if args.command == "migrate":
        return cmd_migrate(args.db_path)
    elif args.command == "run":
        return cmd_run(args.config_dir, args.db_path)
    elif args.command == "export-report":
        return cmd_export_report(args.db_path, args.out)
    return 0

if __name__ == "__main__":
    sys.exit(main())
