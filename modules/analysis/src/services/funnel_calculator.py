import math
import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import aggregation_queries

class FunnelCalculator:
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

    def run_funnel_analysis(self, days: int) -> Dict[str, Any]:
        start_time, end_time = self.get_lookback_window(days)
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Funnel stage counts
        counts = aggregation_queries.get_funnel_counts(self.conn, start_time, end_time, self.target_languages)

        # 2. Stage conversions and cumulative yield calculations
        # Stages sequence: ingested -> classified -> curated -> approved -> translated -> published
        stages_seq = [
            ("ingested", counts["ingested"]),
            ("classified", counts["classified"]),
            ("curated", counts["curated"]),
            ("approved", counts["approved"]),
            ("translated", counts["translated"]),
            ("published", counts["published"])
        ]

        breakdowns = []
        prev_count = None
        ingested_count = counts["ingested"]

        for idx, (stage_name, cnt) in enumerate(stages_seq):
            if idx == 0:
                stage_conversion_rate = 1.0 if ingested_count > 0 else None
                cumulative_yield = 1.0 if ingested_count > 0 else None
            else:
                stage_conversion_rate = (cnt / prev_count) if prev_count and prev_count > 0 else (None if prev_count == 0 else None)
                cumulative_yield = (cnt / ingested_count) if ingested_count > 0 else None

            breakdowns.append({
                "stage": stage_name,
                "count": cnt,
                "stage_conversion_rate": stage_conversion_rate,
                "cumulative_yield": cumulative_yield
            })
            prev_count = cnt

        # 3. Latency lists retrieval and percentile calculations
        latency_types = {
            "feed_freshness_delay": aggregation_queries.get_feed_freshness_delays(self.conn, start_time, end_time),
            "fetch_execution_latency": aggregation_queries.get_fetch_execution_latencies(self.conn, start_time, end_time),
            "classification_delay": aggregation_queries.get_classification_delays(self.conn, start_time, end_time),
            "curation_delay": aggregation_queries.get_curation_delays(self.conn, start_time, end_time),
            "translation_delay": aggregation_queries.get_translation_delays(self.conn, start_time, end_time),
            "publish_delay": aggregation_queries.get_publish_delays(self.conn, start_time, end_time),
            "pipeline_lead_time": aggregation_queries.get_e2e_latencies(self.conn, start_time, end_time)
        }

        latency_metrics = {}
        for key, latencies in latency_types.items():
            latency_metrics[key] = {
                "average": self.get_average(latencies),
                "median": self.get_percentile(latencies, 0.5),
                "p90": self.get_percentile(latencies, 0.9)
            }

        # 4. Published by language calculations
        lang_rows = aggregation_queries.get_published_by_language_stats(self.conn, start_time, end_time)
        total_approved = aggregation_queries.get_total_approved_articles_count(self.conn, start_time, end_time)

        published_by_lang_map = {row["language_code"]: row["published_count"] for row in lang_rows}
        published_by_language = []
        for lang in self.target_languages:
            cnt = published_by_lang_map.get(lang, 0)
            cov_rate = (cnt / total_approved) if total_approved > 0 else None
            published_by_language.append({
                "language_code": lang,
                "published_count": cnt,
                "coverage_rate": cov_rate
            })

        return {
            "report_type": "funnel",
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "window_start": start_time,
            "window_end": end_time,
            "metrics": {
                "total_ingested": counts["ingested"],
                "low_context_bypass_count": counts["low_context_bypass"],
                "total_classified": counts["classified"],
                "relevant_classified": counts["classified_relevant"],
                "total_curated": counts["curated"],
                "curation_approved": counts["approved"],
                "total_translated": counts["translated"],
                "total_published": counts["published"],
                "pipeline_lead_time_seconds": latency_metrics["pipeline_lead_time"]
            },
            "stage_latency_breakdown_seconds": {
                "feed_freshness_delay": latency_metrics["feed_freshness_delay"],
                "fetch_execution_latency": latency_metrics["fetch_execution_latency"],
                "classification_delay": latency_metrics["classification_delay"],
                "curation_delay": latency_metrics["curation_delay"],
                "translation_delay": latency_metrics["translation_delay"],
                "publish_delay": latency_metrics["publish_delay"]
            },
            "breakdowns": breakdowns,
            "published_by_language": published_by_language
        }

    def format_markdown_report(self, data: Dict[str, Any]) -> str:
        window_start = data["window_start"]
        window_end = data["window_end"]
        days = data["lookback_days"]
        metrics = data["metrics"]
        breakdowns = data["breakdowns"]
        latencies = data["stage_latency_breakdown_seconds"]
        published_by_lang = data["published_by_language"]

        def format_pct(val: Optional[float]) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val * 100:.2f}%"

        def format_sec(val: Optional[float]) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val:.2f}s"

        lines = [
            "# Pipeline Funnel Conversion & Bottleneck Report",
            "",
            f"**Generated At**: {data['generated_at']}",
            f"**Lookback Period**: {days} days ({window_start} to {window_end})",
            "",
            "## Overall Pipeline Throughput",
            f"- **Total Ingested**: {metrics['total_ingested']}",
            f"- **Low-Context Bypass**: {metrics['low_context_bypass_count']}",
            f"- **Total Classified**: {metrics['total_classified']}",
            f"- **Relevant Classified**: {metrics['relevant_classified']}",
            f"- **Total Curated**: {metrics['total_curated']}",
            f"- **Curation Approved**: {metrics['curation_approved']}",
            f"- **Total Translated**: {metrics['total_translated']}",
            f"- **Total Published**: {metrics['total_published']}",
            "",
            "## Funnel Stage Conversion",
            "| Stage | Count | Stage Conversion Rate | Cumulative Yield |",
            "| :--- | :--- | :--- | :--- |"
        ]

        for item in breakdowns:
            stage_name = item["stage"].capitalize()
            cnt = item["count"]
            conv = format_pct(item["stage_conversion_rate"])
            cum = format_pct(item["cumulative_yield"])
            lines.append(f"| {stage_name} | {cnt} | {conv} | {cum} |")

        lines.extend([
            "",
            "## Stage Latency Analysis (Seconds)",
            "| Delay Type / Pipeline Stage | Avg Latency | Median (p50) | 90th Percentile (p90) |",
            "| :--- | :--- | :--- | :--- |"
        ])

        e2e = metrics["pipeline_lead_time_seconds"]
        lines.append(f"| E2E Pipeline Lead Time | {format_sec(e2e['average'])} | {format_sec(e2e['median'])} | {format_sec(e2e['p90'])} |")

        stages_latencies = [
            ("Feed Freshness Delay", latencies["feed_freshness_delay"]),
            ("Fetch Execution Latency", latencies["fetch_execution_latency"]),
            ("Classification Delay", latencies["classification_delay"]),
            ("Curation Delay", latencies["curation_delay"]),
            ("Translation Delay", latencies["translation_delay"]),
            ("Publish Delay", latencies["publish_delay"])
        ]

        for label, val in stages_latencies:
            lines.append(f"| {label} | {format_sec(val['average'])} | {format_sec(val['median'])} | {format_sec(val['p90'])} |")

        lines.extend([
            "",
            "## Published Content by Language",
            "| Language Code | Published Count | Coverage Rate |",
            "| :--- | :--- | :--- |"
        ])

        for item in published_by_lang:
            lang = item["language_code"]
            cnt = item["published_count"]
            cov = format_pct(item["coverage_rate"])
            lines.append(f"| {lang} | {cnt} | {cov} |")

        return "\n".join(lines) + "\n"
