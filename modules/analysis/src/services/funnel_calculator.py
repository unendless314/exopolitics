import math
import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import aggregation_queries

class FunnelCalculator:
    def __init__(self, db_conn: sqlite3.Connection, target_languages: Optional[List[str]] = None, maturation_offset_hours: int = 2):
        self.conn = db_conn
        self.target_languages = target_languages or ["en", "zh", "ja"]
        self.maturation_offset_hours = maturation_offset_hours

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
        # Define Raw Window
        now = datetime.datetime.now(datetime.timezone.utc)
        raw_end = now
        raw_start = raw_end - datetime.timedelta(days=days)

        # Define Matured Window
        matured_end = raw_end - datetime.timedelta(hours=self.maturation_offset_hours)
        matured_start = raw_start - datetime.timedelta(hours=self.maturation_offset_hours)

        raw_start_str = raw_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        raw_end_str = raw_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        matured_start_str = matured_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        matured_end_str = matured_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Raw Window funnel counts & classification readiness
        raw_counts = aggregation_queries.get_funnel_counts(self.conn, raw_start_str, raw_end_str, self.target_languages)
        raw_readiness = aggregation_queries.get_classification_readiness_breakdown(self.conn, raw_start_str, raw_end_str)

        # 2. Matured Window funnel counts & classification readiness
        matured_counts = aggregation_queries.get_funnel_counts(self.conn, matured_start_str, matured_end_str, self.target_languages)
        matured_readiness = aggregation_queries.get_classification_readiness_breakdown(self.conn, matured_start_str, matured_end_str)

        # 3. Latency lists retrieval under RAW window (no maturation offset)
        raw_latencies = {
            "feed_freshness_delay": aggregation_queries.get_feed_freshness_delays(self.conn, raw_start_str, raw_end_str),
            "fetch_execution_latency": aggregation_queries.get_fetch_execution_latencies(self.conn, raw_start_str, raw_end_str),
            "classification_delay": aggregation_queries.get_classification_delays(self.conn, raw_start_str, raw_end_str),
            "curation_delay": aggregation_queries.get_curation_delays(self.conn, raw_start_str, raw_end_str),
            "translation_delay": aggregation_queries.get_translation_delays(self.conn, raw_start_str, raw_end_str),
            "publish_delay": aggregation_queries.get_publish_delays(self.conn, raw_start_str, raw_end_str),
            "pipeline_lead_time": aggregation_queries.get_e2e_latencies(self.conn, raw_start_str, raw_end_str)
        }

        # 4. Published by language calculations under MATURED window
        lang_rows = aggregation_queries.get_published_by_language_stats(self.conn, matured_start_str, matured_end_str)
        total_approved = aggregation_queries.get_total_approved_articles_count(self.conn, matured_start_str, matured_end_str)

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

        # 5. Data Quality Anomalies under RAW window
        anomalies = aggregation_queries.get_data_quality_anomalies(self.conn, raw_start_str, raw_end_str)

        def get_rate(num, denom):
            if denom is None or denom == 0 or num is None:
                return None
            return num / denom

        return {
            "report_type": "funnel",
            "schema_version": "3.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "maturation_offset_hours": self.maturation_offset_hours,
            "raw_window": {
                "start": raw_start_str,
                "end": raw_end_str
            },
            "matured_window": {
                "start": matured_start_str,
                "end": matured_end_str
            },
            "raw_metrics": {
                "total_ingested": raw_readiness["total_ingested"],
                "low_context_observation_count": raw_readiness["low_context_observation_count"],
                "total_classified": raw_readiness["total_classified"],
                "relevant_classified": raw_counts["classified_relevant"],
                "total_curated": raw_counts["curated"],
                "curation_approved": raw_counts["approved"],
                "total_translated": raw_counts["translated"],
                "total_published": raw_counts["published"],
                "classification_readiness_breakdown": {
                    "low_context_observation_count": raw_readiness["low_context_observation_count"],
                    "total_classified": raw_readiness["total_classified"],
                    "pending_classification": raw_readiness["pending_classification"],
                    "failed_text_processing": raw_readiness["failed_text_processing"],
                    "missing_text_processing": raw_readiness["missing_text_processing"]
                }
            },
            "matured_metrics": {
                "total_ingested": matured_readiness["total_ingested"],
                "low_context_observation_count": matured_readiness["low_context_observation_count"],
                "total_classified": matured_readiness["total_classified"],
                "relevant_classified": matured_counts["classified_relevant"],
                "total_curated": matured_counts["curated"],
                "curation_approved": matured_counts["approved"],
                "total_translated": matured_counts["translated"],
                "total_published": matured_counts["published"],
                "classification_rate": get_rate(matured_readiness["total_classified"], matured_readiness["total_ingested"]),
                "curation_rate": get_rate(matured_counts["curated"], matured_readiness["total_classified"]),
                "curation_approval_rate": get_rate(matured_counts["approved"], matured_counts["curated"]),
                "translation_rate": get_rate(matured_counts["translated"], matured_counts["approved"]),
                "publish_rate": get_rate(matured_counts["published"], matured_counts["approved"]),
                "classification_readiness_breakdown": {
                    "low_context_observation_count": matured_readiness["low_context_observation_count"],
                    "total_classified": matured_readiness["total_classified"],
                    "pending_classification": matured_readiness["pending_classification"],
                    "failed_text_processing": matured_readiness["failed_text_processing"],
                    "missing_text_processing": matured_readiness["missing_text_processing"]
                }
            },
            "raw_latency_metrics": {
                "pipeline_lead_time_seconds": {
                    "average": self.get_average(raw_latencies["pipeline_lead_time"]),
                    "median": self.get_percentile(raw_latencies["pipeline_lead_time"], 0.5),
                    "p90": self.get_percentile(raw_latencies["pipeline_lead_time"], 0.9)
                },
                "stage_latency_breakdown_seconds": {
                    "feed_freshness_delay": {
                        "average": self.get_average(raw_latencies["feed_freshness_delay"]),
                        "median": self.get_percentile(raw_latencies["feed_freshness_delay"], 0.5),
                        "p90": self.get_percentile(raw_latencies["feed_freshness_delay"], 0.9)
                    },
                    "fetch_execution_latency": {
                        "average": self.get_average(raw_latencies["fetch_execution_latency"]),
                        "median": self.get_percentile(raw_latencies["fetch_execution_latency"], 0.5),
                        "p90": self.get_percentile(raw_latencies["fetch_execution_latency"], 0.9)
                    },
                    "classification_delay": {
                        "average": self.get_average(raw_latencies["classification_delay"]),
                        "median": self.get_percentile(raw_latencies["classification_delay"], 0.5),
                        "p90": self.get_percentile(raw_latencies["classification_delay"], 0.9)
                    },
                    "curation_delay": {
                        "average": self.get_average(raw_latencies["curation_delay"]),
                        "median": self.get_percentile(raw_latencies["curation_delay"], 0.5),
                        "p90": self.get_percentile(raw_latencies["curation_delay"], 0.9)
                    },
                    "translation_delay": {
                        "average": self.get_average(raw_latencies["translation_delay"]),
                        "median": self.get_percentile(raw_latencies["translation_delay"], 0.5),
                        "p90": self.get_percentile(raw_latencies["translation_delay"], 0.9)
                    },
                    "publish_delay": {
                        "average": self.get_average(raw_latencies["publish_delay"]),
                        "median": self.get_percentile(raw_latencies["publish_delay"], 0.5),
                        "p90": self.get_percentile(raw_latencies["publish_delay"], 0.9)
                    }
                }
            },
            "published_by_language": published_by_language,
            "data_quality_anomalies": anomalies
        }

    def format_markdown_report(self, data: Dict[str, Any]) -> str:
        raw_window = data["raw_window"]
        matured_window = data["matured_window"]
        days = data["lookback_days"]
        offset = data["maturation_offset_hours"]

        raw_m = data["raw_metrics"]
        matured_m = data["matured_metrics"]
        latencies = data["raw_latency_metrics"]["stage_latency_breakdown_seconds"]
        e2e = data["raw_latency_metrics"]["pipeline_lead_time_seconds"]
        published_by_lang = data["published_by_language"]
        anomalies = data["data_quality_anomalies"]

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
            f"**Lookback Period**: {days} days",
            f"**Maturation Delay Offset**: {offset} hours",
            "",
            "## 1. Raw Window Cohort Performance",
            f"**Raw Window**: {raw_window['start']} to {raw_window['end']}",
            "",
            "### Throughput Metrics",
            f"- **Total Ingested**: {raw_m['total_ingested']}",
            f"- **Low-Context Observation Count**: {raw_m['low_context_observation_count']}",
            f"- **Total Classified**: {raw_m['total_classified']}",
            f"- **Relevant Classified**: {raw_m['relevant_classified']}",
            f"- **Total Curated**: {raw_m['total_curated']}",
            f"- **Curation Approved**: {raw_m['curation_approved']}",
            f"- **Total Translated**: {raw_m['total_translated']}",
            f"- **Total Published**: {raw_m['total_published']}",
            "",
            "### Ingestion Cohort Readiness Breakdown",
            f"- **Eligible & Classified**: {raw_m['classification_readiness_breakdown']['total_classified']}",
            f"- **Low-Context Observation Count**: {raw_m['classification_readiness_breakdown']['low_context_observation_count']}",
            f"- **Pending Classification**: {raw_m['classification_readiness_breakdown']['pending_classification']}",
            f"- **Failed Text Processing**: {raw_m['classification_readiness_breakdown']['failed_text_processing']}",
            f"- **Missing Text Processing**: {raw_m['classification_readiness_breakdown']['missing_text_processing']}",
            "",
            "## 2. Matured Window Cohort Performance (Conversion Stable)",
            f"**Matured Window**: {matured_window['start']} to {matured_window['end']}",
            "",
            "### Throughput Metrics",
            f"- **Total Ingested**: {matured_m['total_ingested']}",
            f"- **Low-Context Observation Count**: {matured_m['low_context_observation_count']}",
            f"- **Total Classified**: {matured_m['total_classified']}",
            f"- **Relevant Classified**: {matured_m['relevant_classified']}",
            f"- **Total Curated**: {matured_m['total_curated']}",
            f"- **Curation Approved**: {matured_m['curation_approved']}",
            f"- **Total Translated**: {matured_m['total_translated']}",
            f"- **Total Published**: {matured_m['total_published']}",
            "",
            "### Conversion Rates",
            f"- **Classification Rate**: {format_pct(matured_m['classification_rate'])}",
            f"- **Curation Rate**: {format_pct(matured_m['curation_rate'])}",
            f"- **Curation Approval Rate**: {format_pct(matured_m['curation_approval_rate'])}",
            f"- **Translation Completion Rate**: {format_pct(matured_m['translation_rate'])}",
            f"- **Publication Rate**: {format_pct(matured_m['publish_rate'])}",
            "",
            "### Ingestion Cohort Readiness Breakdown",
            f"- **Eligible & Classified**: {matured_m['classification_readiness_breakdown']['total_classified']}",
            f"- **Low-Context Observation Count**: {matured_m['classification_readiness_breakdown']['low_context_observation_count']}",
            f"- **Pending Classification**: {matured_m['classification_readiness_breakdown']['pending_classification']}",
            f"- **Failed Text Processing**: {matured_m['classification_readiness_breakdown']['failed_text_processing']}",
            f"- **Missing Text Processing**: {matured_m['classification_readiness_breakdown']['missing_text_processing']}",
            "",
            "## 3. Stage Latency Analysis (Seconds, Raw Window)",
            "",
            "> [!WARNING]",
            "> Latency metrics include system initialization/historical ingestion data and do not reflect steady-state operational SLA.",
            "",
            "| Delay Type / Pipeline Stage | Avg Latency | Median (p50) | 90th Percentile (p90) |",
            "| :--- | :--- | :--- | :--- |"
        ]

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
            "## 4. Published Content by Language (Matured Window)",
            "| Language Code | Published Count | Coverage Rate |",
            "| :--- | :--- | :--- |"
        ])

        for item in published_by_lang:
            lang = item["language_code"]
            cnt = item["published_count"]
            cov = format_pct(item["coverage_rate"])
            lines.append(f"| {lang} | {cnt} | {cov} |")

        lines.extend([
            "",
            "## 5. Data Quality Diagnostics"
        ])

        if anomalies:
            for anomaly in anomalies:
                lines.append(f"- **{anomaly['code']}**: {anomaly['count']} occurrences (Item samples: {anomaly['item_samples']})")
        else:
            lines.append("No data quality anomalies detected.")

        return "\n".join(lines) + "\n"
