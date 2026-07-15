import json
import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import translate_queries

class TranslateService:
    def __init__(self, db_conn: sqlite3.Connection, target_languages: Optional[List[str]] = None):
        self.conn = db_conn
        self.target_languages = target_languages or ["en", "zh", "ja"]

    def get_lookback_window(self, days: int) -> tuple[str, str]:
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(days=days)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def run_translate_analysis(self, days: int) -> Dict[str, Any]:
        start_time, end_time = self.get_lookback_window(days)
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        target_langs_json = json.dumps(self.target_languages)

        # 1. Fetch overall KPIs
        overall_success = translate_queries.get_overall_translation_success_rate(self.conn, start_time, end_time)
        overall_completion = translate_queries.get_overall_translation_completion_rate(
            self.conn, start_time, end_time, target_langs_json
        )
        overall_latency = translate_queries.get_overall_translation_latency(self.conn, start_time, end_time)

        # 2. Fetch breakdown data grouped by language_code
        success_stale_rows = translate_queries.get_translation_success_and_stale_rates(self.conn, start_time, end_time)
        completion_rows = translate_queries.get_translation_completion_rates(
            self.conn, start_time, end_time, target_langs_json
        )
        latency_rows = translate_queries.get_translation_latencies(self.conn, start_time, end_time)
        char_vol_rows = translate_queries.get_translation_char_volumes(self.conn, start_time, end_time)

        # Map breakdown queries by language_code
        success_stale_map = {row["language_code"]: row for row in success_stale_rows}
        completion_map = {row["language_code"]: row["completion_rate"] for row in completion_rows}
        latency_map = {row["language_code"]: row["avg_latency"] for row in latency_rows}
        char_vol_map = {row["language_code"]: row["char_volume"] for row in char_vol_rows}

        # Build breakdowns for each configured target language
        breakdowns = []
        for lang in self.target_languages:
            row_ss = success_stale_map.get(lang)
            success_rate = row_ss["success_rate"] if row_ss else None
            stale_rate = row_ss["stale_rate"] if row_ss else None

            completion_rate = completion_map.get(lang, None)
            avg_latency = latency_map.get(lang, None)
            char_volume = char_vol_map.get(lang, 0)
            if char_volume is None:
                char_volume = 0

            breakdowns.append({
                "language_code": lang,
                "translation_success_rate": success_rate,
                "translation_completion_rate": completion_rate,
                "average_latency_seconds": avg_latency,
                "stale_rate": stale_rate,
                "translation_character_volume_proxy": int(char_volume)
            })

        return {
            "report_type": "translation",
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "window_start": start_time,
            "window_end": end_time,
            "metrics": {
                "overall_translation_success_rate": overall_success,
                "overall_translation_completion_rate": overall_completion,
                "average_latency_seconds": overall_latency
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
            "# Translation Performance & Queue Report",
            "",
            f"**Generated At**: {data['generated_at']}",
            f"**Lookback Period**: {days} days ({window_start} to {window_end})",
            "",
            "## Overall Translation KPIs",
            f"- **Overall Translation Success Rate**: {format_pct(metrics['overall_translation_success_rate'])}",
            f"- **Overall Translation Completion Rate**: {format_pct(metrics['overall_translation_completion_rate'])}",
            f"- **Average Latency**: {format_sec(metrics['average_latency_seconds'])}",
            "",
            "## Language Performance Breakdown",
            "| Language Code | Success Rate | Completion Rate | Avg Latency | Stale Rate | Character Volume Proxy |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"
        ]

        for item in breakdowns:
            lang = item["language_code"]
            success = format_pct(item["translation_success_rate"])
            completion = format_pct(item["translation_completion_rate"])
            latency = format_sec(item["average_latency_seconds"])
            stale = format_pct(item["stale_rate"])
            char_vol = item["translation_character_volume_proxy"]

            lines.append(
                f"| {lang} | {success} | {completion} | {latency} | {stale} | {char_vol} |"
            )

        return "\n".join(lines) + "\n"
