import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import aggregation_queries
from modules.analysis.src.services.source_classifier import SourceQuadrantClassifier
from modules.analysis.src.config import SourceMeta, CategoryMeta

class SourceService:
    def __init__(
        self,
        db_conn: sqlite3.Connection,
        sources_meta: Optional[Dict[int, SourceMeta]] = None,
        categories_meta: Optional[Dict[int, CategoryMeta]] = None,
        yield_threshold: float = 0.10,
        relevance_threshold: float = 0.40,
        fetch_isolation_threshold: float = 0.50
    ):
        self.conn = db_conn
        self.sources_meta = sources_meta or {}
        self.categories_meta = categories_meta or {}
        self.classifier = SourceQuadrantClassifier(
            yield_threshold=yield_threshold,
            relevance_threshold=relevance_threshold,
            fetch_isolation_threshold=fetch_isolation_threshold
        )

    def get_lookback_window(self, days: int) -> tuple[str, str]:
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(days=days)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def run_sources_analysis(self, days: int) -> Dict[str, Any]:
        start_time, end_time = self.get_lookback_window(days)
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Fetch overall KPIs
        overall_fetch_rate = aggregation_queries.get_overall_fetch_success_rate(self.conn, start_time, end_time)
        ingest_metrics = aggregation_queries.get_overall_ingest_metrics(self.conn, start_time, end_time)

        # 2. Fetch breakdowns data from DB
        fetch_stats = aggregation_queries.get_sources_fetch_stats(self.conn, start_time, end_time)
        cohort_stats = aggregation_queries.get_sources_cohort_stats(self.conn, start_time, end_time)
        reason_stats = aggregation_queries.get_sources_reason_distributions(self.conn, start_time, end_time)

        # Map raw query outputs by source_id
        fetch_map = {row["source_id"]: row["fetch_success_rate"] for row in fetch_stats}
        cohort_map = {row["source_id"]: row for row in cohort_stats}
        
        reason_map = {}
        valid_reasons = {
            "missing_body", "sanitizer_exception", "post_cleanup_empty",
            "too_short", "title_only", "title_heavy", "template_heavy",
            "mostly_links", "truncated_to_low_context"
        }
        for row in reason_stats:
            src_id = row["source_id"]
            reason = row["text_processing_reason"]
            count = row["reason_count"]
            if reason in valid_reasons and count > 0:
                if src_id not in reason_map:
                    reason_map[src_id] = {}
                reason_map[src_id][reason] = count

        # Union of source IDs from config and database
        all_source_ids = set(self.sources_meta.keys()) | set(fetch_map.keys()) | set(cohort_map.keys())

        breakdowns = []
        for src_id in sorted(all_source_ids):
            # Check if unknown
            is_unknown = src_id not in self.sources_meta
            src_meta = self.sources_meta.get(src_id)
            category_id = src_meta.category_id if src_meta else None

            # Get fetch health
            fetch_success_rate = fetch_map.get(src_id, None)

            # Get cohort metrics
            c_row = cohort_map.get(src_id)
            if c_row:
                ingest_volume = c_row["ingest_volume"] or 0
                relevance_rate = c_row["relevance_rate"]
                curation_approval_rate = c_row["curation_approval_rate"]
                overall_yield = c_row["overall_yield"]
                class_char_vol = c_row["classification_char_volume_proxy"] or 0
                curate_char_vol = c_row["curation_char_volume_proxy"] or 0
                classified_count = c_row["classified_count"] or 0
                curate_approved_count = c_row["curate_approved_count"] or 0
                
                # Proportions
                prop_core = c_row["prop_core"]
                prop_adjacent = c_row["prop_adjacent"]
                prop_irrelevant = c_row["prop_irrelevant"]
                prop_unknown = c_row["prop_unknown"]
            else:
                ingest_volume = 0
                relevance_rate = None
                curation_approval_rate = None
                overall_yield = None
                class_char_vol = 0
                curate_char_vol = 0
                classified_count = 0
                curate_approved_count = 0
                prop_core = None
                prop_adjacent = None
                prop_irrelevant = None
                prop_unknown = None

            # Calculate classification filtering overhead
            # Formula: Total Classified / NULLIF(Curate Approved, 0)
            if curate_approved_count > 0:
                classification_filtering_overhead = classified_count / curate_approved_count
            else:
                classification_filtering_overhead = None

            # Topic class breakdown
            topic_class_breakdown = {
                "core": prop_core,
                "adjacent": prop_adjacent,
                "irrelevant": prop_irrelevant,
                "unknown": prop_unknown
            }

            # Classifier call
            quadrant, flags = self.classifier.classify(
                fetch_success_rate=fetch_success_rate,
                ingest_volume=ingest_volume,
                relevance_rate=relevance_rate,
                overall_yield=overall_yield,
                category_id=category_id,
                is_unknown_source=is_unknown
            )

            # Build breakdown item
            item = {
                "source_id": src_id,
                "fetch_success_rate": fetch_success_rate,
                "ingest_volume": ingest_volume,
                "relevance_rate": relevance_rate,
                "curation_approval_rate": curation_approval_rate,
                "overall_yield": overall_yield,
                "classification_character_volume_proxy": class_char_vol,
                "curation_character_volume_proxy": curate_char_vol,
                "classification_filtering_overhead": classification_filtering_overhead,
                "topic_class_breakdown": topic_class_breakdown,
                "decision_model": {
                    "quadrant": quadrant,
                    "analysis_flags": flags
                }
            }

            # Optional text processing reason distribution
            if src_id in reason_map:
                item["text_processing_reason_distribution"] = reason_map[src_id]

            breakdowns.append(item)

        return {
            "report_type": "sources",
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "window_start": start_time,
            "window_end": end_time,
            "metrics": {
                "overall_fetch_success_rate": overall_fetch_rate,
                "total_ingested_items": ingest_metrics["total_ingested_items"],
                "low_context_bypass_rate": ingest_metrics["low_context_bypass_rate"]
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

        def format_float(val: Optional[float], decimals: int = 4) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val:.{decimals}f}"

        lines = [
            "# RSS Source Connection & Content Quality Report",
            "",
            f"**Generated At**: {data['generated_at']}",
            f"**Lookback Period**: {days} days ({window_start} to {window_end})",
            "",
            "## Overall Pipeline KPIs",
            f"- **Overall Fetch Success Rate**: {format_pct(metrics['overall_fetch_success_rate'])}",
            f"- **Total Ingested Items**: {metrics['total_ingested_items']}",
            f"- **Low-Context Bypass Rate**: {format_pct(metrics['low_context_bypass_rate'])}",
            "",
            "## Source Performance Breakdown",
            "| Source ID | Source Title | Fetch Success | Ingest Vol | Relevance | Curation Approval | Overall Yield | Quadrant | Flags |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]

        for item in breakdowns:
            src_id = item["source_id"]
            fetch_rate = format_pct(item["fetch_success_rate"])
            vol = item["ingest_volume"]
            rel = format_pct(item["relevance_rate"])
            cur = format_pct(item["curation_approval_rate"])
            yld = format_pct(item["overall_yield"])
            
            quadrant = item["decision_model"]["quadrant"]
            quadrant_str = quadrant.upper() if quadrant else "N/A"
            flags = item["decision_model"]["analysis_flags"]
            flags_str = ", ".join(f"[{f}]" for f in flags) if flags else "None"

            # Resolve source title
            if src_id in self.sources_meta:
                title = self.sources_meta[src_id].title
            else:
                title = f"Unknown Source (ID: {src_id})"

            lines.append(
                f"| {src_id} | {title} | {fetch_rate} | {vol} | {rel} | {cur} | {yld} | {quadrant_str} | {flags_str} |"
            )

        return "\n".join(lines) + "\n"
