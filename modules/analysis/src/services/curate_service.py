import math
import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import curate_queries

class CurateService:
    def __init__(self, db_conn: sqlite3.Connection):
        self.conn = db_conn

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

    def run_curate_analysis(self, days: int) -> Dict[str, Any]:
        start_time, end_time = self.get_lookback_window(days)
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        approval_rate = curate_queries.get_curation_approval_rate(self.conn, start_time, end_time)
        char_vol_proxy = curate_queries.get_curation_char_volume_proxy(self.conn, start_time, end_time)
        delays = curate_queries.get_curation_delays(self.conn, start_time, end_time)

        rejection_rows = curate_queries.get_curation_rejection_mix(self.conn, start_time, end_time)
        rejection_mix = []
        for row in rejection_rows:
            rejection_mix.append({
                "downstream_action": row["downstream_action"],
                "count": row["count"]
            })

        return {
            "report_type": "curation_diagnostics",
            "schema_version": "2.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "window_start": start_time,
            "window_end": end_time,
            "metrics": {
                "curation_approval_rate": approval_rate,
                "curation_character_volume_proxy": char_vol_proxy,
                "curation_latency_seconds": {
                    "average": self.get_average(delays),
                    "median": self.get_percentile(delays, 0.5),
                    "p90": self.get_percentile(delays, 0.9)
                }
            },
            "curation_rejection_mix": rejection_mix,
            "breakdowns": []
        }

    def format_markdown_report(self, data: Dict[str, Any]) -> str:
        window_start = data["window_start"]
        window_end = data["window_end"]
        days = data["lookback_days"]
        metrics = data["metrics"]
        rejection_mix = data["curation_rejection_mix"]
        delay = metrics["curation_latency_seconds"]

        def format_pct(val: Optional[float]) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val * 100:.2f}%"

        def format_sec(val: Optional[float]) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val:.2f}s"

        lines = [
            "# Curation Performance & Diagnostics Report",
            "",
            f"**Generated At**: {data['generated_at']}",
            f"**Lookback Period**: {days} days ({window_start} to {window_end})",
            "",
            "## Curation KPIs",
            f"- **Curation Approval Rate**: {format_pct(metrics['curation_approval_rate'])}",
            f"- **Curation Character Volume Proxy**: {metrics['curation_character_volume_proxy']}",
            "",
            "## Curation Delay Analysis",
            f"- **Average Delay**: {format_sec(delay['average'])}",
            f"- **Median Delay (p50)**: {format_sec(delay['median'])}",
            f"- **90th Percentile Delay (p90)**: {format_sec(delay['p90'])}",
            "",
            "## Curation Rejection Mix",
        ]

        if rejection_mix:
            for item in rejection_mix:
                action = item["downstream_action"] or "unknown"
                cnt = item["count"]
                lines.append(f"- **{action}**: {cnt}")
        else:
            lines.append("No curation rejections recorded.")

        return "\n".join(lines) + "\n"
