import json
import math
import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import publish_queries

class PublishService:
    def __init__(self, db_conn: sqlite3.Connection, target_languages: Optional[List[str]] = None):
        self.conn = db_conn
        self.target_languages = target_languages or ["en", "zh", "ja"]

    def get_lookback_window(self, days: int) -> tuple[str, str]:
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(days=days)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_percentile(self, data: List[int], pct: float) -> Optional[float]:
        if not data:
            return None
        sorted_data = sorted(data)
        n = len(sorted_data)
        idx = pct * (n - 1)
        idx_floor = math.floor(idx)
        idx_ceil = math.ceil(idx)
        if idx_floor == idx_ceil:
            return float(sorted_data[int(idx)])
        d0 = sorted_data[idx_floor] * (idx_ceil - idx)
        d1 = sorted_data[idx_ceil] * (idx - idx_floor)
        return float(d0 + d1)

    def get_average(self, data: List[int]) -> Optional[float]:
        if not data:
            return None
        return sum(data) / len(data)

    def run_publish_analysis(self, days: int) -> Dict[str, Any]:
        start_time, end_time = self.get_lookback_window(days)
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        target_langs_json = json.dumps(self.target_languages)

        # 1. Overall Metrics
        publish_count = publish_queries.get_publish_count(self.conn, start_time, end_time)

        # 2. Language-level Metrics
        cov_rows = publish_queries.get_language_coverage_rates(self.conn, start_time, end_time, target_langs_json)
        cov_map = {row["language_code"]: row["coverage_rate"] for row in cov_rows}

        delay_rows = publish_queries.get_publish_delays(self.conn, start_time, end_time)
        delay_map = {}
        for row in delay_rows:
            lang = row["language_code"]
            latency = row["latency"]
            if lang not in delay_map:
                delay_map[lang] = []
            delay_map[lang].append(latency)

        breakdowns = []
        for lang in self.target_languages:
            coverage = cov_map.get(lang, None)
            lang_delays = delay_map.get(lang, [])

            breakdowns.append({
                "language_code": lang,
                "language_coverage_rate": coverage,
                "publish_delay_seconds": {
                    "average": self.get_average(lang_delays),
                    "median": self.get_percentile(lang_delays, 0.5),
                    "p90": self.get_percentile(lang_delays, 0.9)
                }
            })

        return {
            "report_type": "publishing_diagnostics",
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "window_start": start_time,
            "window_end": end_time,
            "metrics": {
                "publish_count": publish_count
            },
            "breakdowns": breakdowns
        }

    def format_markdown_report(self, data: Dict[str, Any]) -> str:
        window_start = data["window_start"]
        window_end = data["window_end"]
        days = data["lookback_days"]
        metrics = data["metrics"]
        breakdowns = data["breakdowns"]

        def format_pct(val: Optional[float]) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val * 100:.2f}%"

        def format_sec(val: Optional[float]) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val:.2f}s"

        lines = [
            "# Publishing Performance & Diagnostics Report",
            "",
            f"**Generated At**: {data['generated_at']}",
            f"**Lookback Period**: {days} days ({window_start} to {window_end})",
            "",
            "## Publishing KPIs",
            f"- **Publish Count**: {metrics['publish_count']}",
            "",
            "## Language Publication Breakdown",
            "| Language Code | Language Coverage Rate | Avg Publish Delay | Median Publish Delay | p90 Publish Delay |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]

        for item in breakdowns:
            lang = item["language_code"]
            cov = format_pct(item["language_coverage_rate"])
            delay = item["publish_delay_seconds"]
            avg_d = format_sec(delay["average"])
            med_d = format_sec(delay["median"])
            p90_d = format_sec(delay["p90"])

            lines.append(
                f"| {lang} | {cov} | {avg_d} | {med_d} | {p90_d} |"
            )

        return "\n".join(lines) + "\n"
