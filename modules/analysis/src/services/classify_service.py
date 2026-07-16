import datetime
import sqlite3
from typing import Dict, Any, List, Optional
from modules.analysis.src.queries import classify_queries
from modules.analysis.src.config import SourceMeta, CategoryMeta

class ClassifyService:
    def __init__(
        self,
        db_conn: sqlite3.Connection,
        sources_meta: Optional[Dict[int, SourceMeta]] = None,
        categories_meta: Optional[Dict[int, CategoryMeta]] = None
    ):
        self.conn = db_conn
        self.sources_meta = sources_meta or {}
        self.categories_meta = categories_meta or {}

    def get_lookback_window(self, days: int) -> tuple[str, str]:
        """
        Returns (start, end) ISO8601 UTC strings.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(days=days)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def run_classify_analysis(self, days: int) -> Dict[str, Any]:
        """
        Runs database queries and computes the structured dictionary for classification metrics.
        Matches the JSON contract for the 'classify' report family.
        """
        start_time, end_time = self.get_lookback_window(days)
        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Fetch overall metrics
        overall_row = classify_queries.get_overall_classify_metrics(self.conn, start_time, end_time)
        
        total_classified = 0
        relevance_rate = None
        average_confidence = None
        prop_core = None
        prop_adjacent = None
        prop_irrelevant = None
        prop_unknown = None

        if overall_row:
            total_classified = overall_row["total_classified"] or 0
            relevance_rate = overall_row["relevance_rate"]
            average_confidence = overall_row["average_confidence"]
            prop_core = overall_row["prop_core"]
            prop_adjacent = overall_row["prop_adjacent"]
            prop_irrelevant = overall_row["prop_irrelevant"]
            prop_unknown = overall_row["prop_unknown"]

        # 2. Fetch character volume proxies
        char_volumes = classify_queries.get_source_char_volumes(self.conn, start_time, end_time)
        char_vol_map = {row["source_id"]: (row["char_volume"] or 0) for row in char_volumes}
        overall_char_vol_proxy = sum(char_vol_map.values())

        # 3. Fetch breakdown metrics
        breakdown_rows = classify_queries.get_source_classify_breakdowns(self.conn, start_time, end_time)
        breakdowns = []

        for row in breakdown_rows:
            src_id = row["source_id"]
            vol = row["classify_volume"] or 0
            
            density_low = row["density_low_rate"] or 0.0
            density_medium = row["density_medium_rate"] or 0.0
            density_high = row["density_high_rate"] or 0.0
            
            # If classify volume is 0, relevance/confidence are None
            src_relevance = row["relevance_rate"] if vol > 0 else None
            src_confidence = row["average_confidence"] if vol > 0 else None

            breakdowns.append({
                "source_id": src_id,
                "classify_volume": vol,
                "classification_character_volume_proxy": char_vol_map.get(src_id, 0),
                "relevance_rate": src_relevance,
                "average_confidence": src_confidence,
                "content_density_distribution": {
                    "low": density_low,
                    "medium": density_medium,
                    "high": density_high
                },
                "topic_class_breakdown": {
                    "core": row["prop_core"] if vol > 0 else None,
                    "adjacent": row["prop_adjacent"] if vol > 0 else None,
                    "irrelevant": row["prop_irrelevant"] if vol > 0 else None,
                    "unknown": row["prop_unknown"] if vol > 0 else None
                }
            })

        return {
            "report_type": "classify",
            "schema_version": "2.0.0",
            "generated_at": generated_at,
            "lookback_days": days,
            "window_start": start_time,
            "window_end": end_time,
            "metrics": {
                "total_classified": total_classified,
                "classification_character_volume_proxy": overall_char_vol_proxy,
                "relevance_rate": relevance_rate,
                "average_confidence": average_confidence,
                "overall_topic_class_breakdown": {
                    "core": prop_core,
                    "adjacent": prop_adjacent,
                    "irrelevant": prop_irrelevant,
                    "unknown": prop_unknown
                }
            },
            "breakdowns": breakdowns
        }

    def format_markdown_report(self, data: Dict[str, Any]) -> str:
        """
        Formats the structured classification metrics dictionary into a readable Markdown report.
        Resolves source titles in memory using sources_meta.
        """
        window_start = data["window_start"]
        window_end = data["window_end"]
        days = data["lookback_days"]
        metrics = data["metrics"]
        breakdowns = data["breakdowns"]

        def format_pct(val: Optional[float]) -> str:
            if val is None:
                return "0.00%"
            return f"{val * 100:.2f}%"

        def format_float(val: Optional[float], decimals: int = 4) -> str:
            if val is None:
                return "[INSUFFICIENT_DATA]"
            return f"{val:.{decimals}f}"

        def format_breakdown(rel: Optional[float], bd: Dict[str, Optional[float]]) -> str:
            if rel is None:
                return "[INSUFFICIENT_DATA]"
            c = format_pct(bd.get("core"))
            a = format_pct(bd.get("adjacent"))
            i = format_pct(bd.get("irrelevant"))
            u = format_pct(bd.get("unknown"))
            return f"{rel * 100:.2f}% ({c} / {a} / {i} / {u})"

        lines = [
            "# LLM Classification Workload Report",
            "",
            f"**Generated At**: {data['generated_at']}",
            f"**Lookback Period**: {days} days ({window_start} to {window_end})",
            "",
            "## Overall Pipeline KPIs",
            f"- **Total Classified Items**: {metrics['total_classified']}",
            f"- **Classification Character Volume Proxy**: {metrics['classification_character_volume_proxy']}",
            f"- **Relevance Breakdown (Core / Adj / Irr / Unk)**: {format_breakdown(metrics['relevance_rate'], metrics['overall_topic_class_breakdown'])}",
            f"- **Average Confidence**: {format_float(metrics['average_confidence'])}",
            "",
            "## Source Performance Breakdown",
            "| Source ID | Source Title | Classify Volume | Classify Char Volume | Relevance Breakdown (Core / Adj / Irr / Unk) | Avg Confidence | Content Density (Low / Med / High) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]

        for item in breakdowns:
            src_id = item["source_id"]
            vol = item["classify_volume"]
            char_vol = item["classification_character_volume_proxy"]
            rel_bd = format_breakdown(item["relevance_rate"], item["topic_class_breakdown"])
            conf = format_float(item["average_confidence"])
            
            density = item["content_density_distribution"]
            density_str = f"{density['low'] * 100:.1f}% / {density['medium'] * 100:.1f}% / {density['high'] * 100:.1f}%"

            # Resolve source title
            if src_id in self.sources_meta:
                title = self.sources_meta[src_id].title
            else:
                title = f"Unknown Source (ID: {src_id}) [INSUFFICIENT_DATA]"

            lines.append(
                f"| {src_id} | {title} | {vol} | {char_vol} | {rel_bd} | {conf} | {density_str} |"
            )

        return "\n".join(lines) + "\n"
