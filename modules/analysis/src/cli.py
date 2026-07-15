import argparse
import json
import logging
import os
import pathlib
import sys
from typing import Optional

import yaml
from modules.analysis.src.config import (
    load_analysis_settings,
    load_sources_config,
    load_categories_config
)
from modules.analysis.src.database import get_connection
from modules.analysis.src.services import ClassifyService, FunnelCalculator, SourceService, TranslateService

logger = logging.getLogger("modules.analysis.cli")

def setup_logging(log_path: Optional[str] = None):
    """
    Configures basic logging to stdout/stderr or to a file.
    Respects the environment variable ANALYSIS_LOG_PATH if set.
    """
    env_log_path = os.environ.get("ANALYSIS_LOG_PATH")
    target_log_path = env_log_path if env_log_path else log_path

    # Get root logger
    root_logger = logging.getLogger()
    # Respect LOG_LEVEL env var, default to INFO
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    root_logger.setLevel(log_level)

    # Clean existing handlers
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    if target_log_path:
        log_file = pathlib.Path(target_log_path)
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_file, encoding="utf-8")
        except Exception as e:
            # Fallback to stderr if file path cannot be written
            handler = logging.StreamHandler(sys.stderr)
            sys.stderr.write(f"Warning: Could not open log file {target_log_path} ({e}). Logging to stderr.\n")
    else:
        handler = logging.StreamHandler(sys.stderr)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

def check_positive_days(value) -> int:
    """
    Validation helper to ensure that `--days` is an integer >= 1.
    """
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer")
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"days lookback window must be at least 1 (got {ivalue})")
    return ivalue

def get_parser() -> argparse.ArgumentParser:
    """
    Constructs the argparse instance.
    Includes parent parser for shared global options to be supported after subcommands.
    """
    # Shared parent parser for common options
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--days",
        type=check_positive_days,
        help="Lookback window in days (default: 7)"
    )
    parent_parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        help="Report output format (default: markdown)"
    )
    parent_parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        help="Directory where report files are written (default: reports/analysis/)"
    )
    parent_parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print report to stdout and suppress file writing"
    )
    parent_parser.add_argument(
        "--db-path",
        type=pathlib.Path,
        help="Path to the SQLite database file (default: data/canonical.db)"
    )

    parser = argparse.ArgumentParser(
        description="Analysis Module Command-Line Interface (UAP Aggregation System)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Subcommands")

    # analyze-classify subcommand (Phase 1 candidate)
    subparsers.add_parser(
        "analyze-classify",
        parents=[parent_parser],
        help="Analyze LLM classification workload volume, relevance rate, and content density"
    )

    # analyze-sources subcommand (Phase 2)
    sources_parser = subparsers.add_parser(
        "analyze-sources",
        parents=[parent_parser],
        help="Analyze RSS source health and content quality"
    )
    sources_parser.add_argument(
        "--yield-threshold",
        type=float,
        help="Overall yield threshold override (default: 0.10)"
    )
    sources_parser.add_argument(
        "--relevance-threshold",
        type=float,
        help="Relevance rate threshold override (default: 0.40)"
    )

    # analyze-funnel subcommand (Phase 2)
    subparsers.add_parser(
        "analyze-funnel",
        parents=[parent_parser],
        help="Analyze pipeline conversion rates, throughput, and bottleneck latencies"
    )

    # analyze-translation subcommand (Phase 3)
    subparsers.add_parser(
        "analyze-translation",
        parents=[parent_parser],
        help="Analyze translation pipeline success rates, character volume, and latency"
    )

    return parser

def main() -> int:
    # Ensure stdout/stderr use UTF-8 to prevent Windows-specific encoding crashes
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    parser = get_parser()
    args = parser.parse_args()

    # Paths resolution
    current_file_dir = pathlib.Path(__file__).parent.resolve()
    workspace_root = current_file_dir.parent.parent.parent
    
    config_path = current_file_dir.parent / "config" / "analysis_settings.yaml"
    
    # Load analysis settings
    try:
        settings = load_analysis_settings(config_path)
    except Exception as e:
        sys.stderr.write(f"Warning: Could not load configuration file {config_path} ({e}). Using defaults.\n")
        settings = None

    # Merge configuration with CLI flags
    days = args.days if args.days is not None else (settings.reporting.defaults.days if settings else 7)
    if days < 1:
        sys.stderr.write(f"Error: days lookback window must be at least 1 (got {days})\n")
        return 1
    fmt = args.format if args.format is not None else (settings.reporting.defaults.format if settings else "markdown")
    output_dir = args.output_dir if args.output_dir is not None else (pathlib.Path(settings.reporting.defaults.output_dir) if settings else pathlib.Path("reports/analysis"))
    stdout = args.stdout if args.stdout else (settings.reporting.defaults.stdout if settings else False)
    db_path = args.db_path if args.db_path is not None else workspace_root / "data" / "canonical.db"
    
    busy_timeout_ms = settings.database.busy_timeout_ms if settings else 10000
    log_path = settings.reporting.defaults.log_path if settings else None

    # Set up logging (respecting settings and environment variables)
    setup_logging(log_path)

    # Verify db existence
    if not db_path.exists():
        logger.error(f"Database path does not exist: {db_path}")
        return 1

    # Connect to the database
    conn = get_connection(db_path, timeout_ms=busy_timeout_ms)

    try:
        # Load external configs for memory-joins
        sources_path = workspace_root / "modules" / "ingest" / "config" / "sources.yaml"
        categories_path = workspace_root / "modules" / "ingest" / "config" / "categories.yaml"
        
        sources_meta = load_sources_config(sources_path)
        categories_meta = load_categories_config(categories_path)

        if args.command == "analyze-classify":
            service = ClassifyService(conn, sources_meta, categories_meta)
            logger.info(f"Running classification analysis with a lookback of {days} days...")
            result = service.run_classify_analysis(days)

            # Format output
            if fmt == "json":
                output_content = json.dumps(result, indent=2)
                filename = "CLASSIFY_MONITOR_REPORT.json"
            else:
                output_content = service.format_markdown_report(result)
                filename = "CLASSIFY_MONITOR_REPORT.md"

        elif args.command == "analyze-sources":
            yield_threshold = args.yield_threshold if (hasattr(args, "yield_threshold") and args.yield_threshold is not None) else (settings.quadrant_classifier.thresholds.overall_yield if settings else 0.10)
            relevance_threshold = args.relevance_threshold if (hasattr(args, "relevance_threshold") and args.relevance_threshold is not None) else (settings.quadrant_classifier.thresholds.relevance_rate if settings else 0.40)
            fetch_isolation_threshold = settings.quadrant_classifier.safeguards.fetch_success_rate_isolation if settings else 0.50

            service = SourceService(
                conn,
                sources_meta,
                categories_meta,
                yield_threshold=yield_threshold,
                relevance_threshold=relevance_threshold,
                fetch_isolation_threshold=fetch_isolation_threshold
            )
            logger.info(f"Running sources analysis with a lookback of {days} days...")
            result = service.run_sources_analysis(days)

            if fmt == "json":
                output_content = json.dumps(result, indent=2)
                filename = "SOURCE_QUALITY_REPORT.json"
            else:
                output_content = service.format_markdown_report(result)
                filename = "SOURCE_QUALITY_REPORT.md"

        elif args.command == "analyze-funnel":
            publish_config_path = workspace_root / "modules" / "publish" / "config" / "publish_settings.yaml"
            target_langs = []
            if publish_config_path.exists():
                try:
                    with open(publish_config_path, "r", encoding="utf-8") as f:
                        t_data = yaml.safe_load(f) or {}
                    target_langs_dict = t_data.get("target_languages", {})
                    if isinstance(target_langs_dict, dict):
                        target_langs = list(target_langs_dict.keys())
                except Exception as e:
                    sys.stderr.write(f"Warning: Could not load target languages from publish config ({e}). Using defaults.\n")
            if not target_langs:
                target_langs = ["en", "zh", "ja"]

            service = FunnelCalculator(conn, target_languages=target_langs)
            logger.info(f"Running funnel analysis with a lookback of {days} days...")
            result = service.run_funnel_analysis(days)

            if fmt == "json":
                output_content = json.dumps(result, indent=2)
                filename = "PIPELINE_FUNNEL_REPORT.json"
            else:
                output_content = service.format_markdown_report(result)
                filename = "PIPELINE_FUNNEL_REPORT.md"

        elif args.command == "analyze-translation":
            translate_config_path = workspace_root / "modules" / "translate" / "config" / "model_settings.yaml"
            target_langs = []
            if translate_config_path.exists():
                try:
                    with open(translate_config_path, "r", encoding="utf-8") as f:
                        t_data = yaml.safe_load(f) or {}
                    target_langs_dict = t_data.get("target_languages", {})
                    if isinstance(target_langs_dict, dict):
                        target_langs = list(target_langs_dict.keys())
                except Exception as e:
                    sys.stderr.write(f"Warning: Could not load target languages from translate config ({e}). Using defaults.\n")
            if not target_langs:
                target_langs = ["en", "zh", "ja"]

            service = TranslateService(conn, target_languages=target_langs)
            logger.info(f"Running translation analysis with a lookback of {days} days...")
            result = service.run_translate_analysis(days)

            if fmt == "json":
                output_content = json.dumps(result, indent=2)
                filename = "TRANSLATION_PERFORMANCE_REPORT.json"
            else:
                output_content = service.format_markdown_report(result)
                filename = "TRANSLATION_PERFORMANCE_REPORT.md"

        else:
            logger.error(f"Unsupported subcommand: {args.command}")
            return 1

        # Emit output (shared for all subcommands)
        if stdout:
            print(output_content, end="")
        else:
            resolved_output_dir = workspace_root / output_dir
            resolved_output_dir.mkdir(parents=True, exist_ok=True)
            report_file_path = resolved_output_dir / filename
            report_file_path.write_text(output_content, encoding="utf-8")
            logger.info(f"Report successfully written to: {report_file_path}")

    except Exception as e:
        logger.exception(f"An unexpected error occurred during execution: {e}")
        return 1
    finally:
        conn.close()

    return 0

if __name__ == "__main__":
    sys.exit(main())
