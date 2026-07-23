import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import ingest_queries

class IngestService:
    def __init__(self, db_conn: sqlite3.Connection):
        self.conn = db_conn

    def get_lookback_window(self, days: int) -> tuple[str, str]:
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(days=days)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def run_ingest_analysis(self, days: int) -> Dict[str, Any]:
        start_time, end_time = self.get_lookback_window(days)
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        overall_fetch_success_rate = ingest_queries.get_overall_fetch_success_rate(self.conn, start_time, end_time)
        run_success_rate = ingest_queries.get_run_success_rate(self.conn, start_time, end_time)
        ingest_volume = ingest_queries.get_ingest_volume(self.conn, start_time, end_time)
        low_context_observation_rate = ingest_queries.get_low_context_observation_rate(self.conn, start_time, end_time)

        error_rows = ingest_queries.get_error_categorization(self.conn, start_time, end_time)
        errors = []
        for row in error_rows:
            errors.append({
                "error_class": row["error_class"],
                "http_status": row["http_status"],
                "error_count": row["error_count"]
            })

        health_rows = ingest_queries.get_rolling_source_health(self.conn)
        source_healths = []
        for row in health_rows:
            source_healths.append({
                "source_id": row["source_id"],
                "health_status": row["health_status"],
                "consecutive_failures": row["consecutive_failures"],
                "last_http_status": row["last_http_status"],
                "last_error_class": row["last_error_class"]
            })

        reason_rows = ingest_queries.get_low_context_reason_distribution(self.conn, start_time, end_time)
        reason_distribution = {}
        for row in reason_rows:
            reason_distribution[row["text_processing_reason"]] = row["reason_count"]

        return {
            "report_type": "ingest_diagnostics",
            "schema_version": "2.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "window_start": start_time,
            "window_end": end_time,
            "metrics": {
                "overall_fetch_success_rate": overall_fetch_success_rate,
                "run_success_rate": run_success_rate,
                "ingest_volume": ingest_volume,
                "low_context_observation_rate": low_context_observation_rate
            },
            "error_categorization": errors,
            "rolling_source_health": source_healths,
            "low_context_reason_distribution": reason_distribution
        }

    def format_markdown_report(self, data: Dict[str, Any]) -> str:
        window_start = data["window_start"]
        window_end = data["window_end"]
        days = data["lookback_days"]
        metrics = data["metrics"]
        errors = data["error_categorization"]
        healths = data["rolling_source_health"]
        reasons = data["low_context_reason_distribution"]

        def format_pct(val: Optional[float]) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val * 100:.2f}%"

        lines = [
            "# Ingestion Performance & Diagnostics Report",
            "",
            f"**Generated At**: {data['generated_at']}",
            f"**Lookback Period**: {days} days ({window_start} to {window_end})",
            "",
            "## Ingest KPIs",
            f"- **Overall Fetch Success Rate**: {format_pct(metrics['overall_fetch_success_rate'])}",
            f"- **Run Success Rate**: {format_pct(metrics['run_success_rate'])}",
            f"- **Ingest Volume**: {metrics['ingest_volume']}",
            f"- **Low-Context Observation Rate**: {format_pct(metrics['low_context_observation_rate'])}",
            "",
            "## Low-Context Reason Distribution",
        ]

        if reasons:
            for reason, cnt in reasons.items():
                lines.append(f"- **{reason}**: {cnt}")
        else:
            lines.append("No low-context observations recorded.")

        lines.extend([
            "",
            "## Error Categorization",
            "| Error Class | HTTP Status | Count |",
            "| :--- | :--- | :--- |"
        ])

        if errors:
            for err in errors:
                h_status = err["http_status"] if err["http_status"] is not None else "N/A"
                lines.append(f"| {err['error_class']} | {h_status} | {err['error_count']} |")
        else:
            lines.append("| None | N/A | 0 |")

        lines.extend([
            "",
            "## Rolling Source Health Snapshot",
            "| Source ID | Health Status | Consecutive Failures | Last HTTP Status | Last Error Class |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ])

        if healths:
            for h in healths:
                last_http = h["last_http_status"] if h["last_http_status"] is not None else "N/A"
                last_err = h["last_error_class"] if h["last_error_class"] is not None else "None"
                lines.append(f"| {h['source_id']} | {h['health_status']} | {h['consecutive_failures']} | {last_http} | {last_err} |")
        else:
            lines.append("| None | N/A | 0 | N/A | None |")

        return "\n".join(lines) + "\n"
