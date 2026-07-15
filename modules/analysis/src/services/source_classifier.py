from typing import List, Tuple, Optional

class SourceQuadrantClassifier:
    def __init__(
        self,
        yield_threshold: float = 0.10,
        relevance_threshold: float = 0.40,
        fetch_isolation_threshold: float = 0.50
    ):
        self.yield_threshold = yield_threshold
        self.relevance_threshold = relevance_threshold
        self.fetch_isolation_threshold = fetch_isolation_threshold

    def classify(
        self,
        fetch_success_rate: Optional[float],
        ingest_volume: int,
        relevance_rate: Optional[float],
        overall_yield: Optional[float],
        category_id: Optional[int],
        is_unknown_source: bool = False
    ) -> Tuple[Optional[str], List[str]]:
        """
        Classifies a source into a quality quadrant and determines analysis flags.
        Returns a tuple of (quadrant_label, list_of_flags).
        """
        flags = []
        
        # 1. Authority Tagging (Visual Indicator Only)
        # Category IDs: 1 (Government & Official Disclosures) and 3 (Scientific Validation & Research)
        if category_id in (1, 3):
            flags.append("AUTHORITY")
            
        if is_unknown_source:
            flags.append("INSUFFICIENT_DATA")

        # 2. Fetch Isolation Rule
        # If fetch success rate is under the isolation threshold (e.g. 50%), isolate the source.
        if fetch_success_rate is not None and fetch_success_rate < self.fetch_isolation_threshold:
            flags.append("CONNECTION_DIAGNOSTICS")
            return None, flags

        # 3. Operational Thresholds & Null-Handling
        # Zero Ingestion: Ingest Volume = 0
        if ingest_volume <= 0:
            if "INSUFFICIENT_DATA" not in flags:
                flags.append("INSUFFICIENT_DATA")
            return None, flags

        # If relevance_rate or overall_yield is None, we also treat it as insufficient data.
        if relevance_rate is None or overall_yield is None:
            if "INSUFFICIENT_DATA" not in flags:
                flags.append("INSUFFICIENT_DATA")
            return None, flags

        # 4. Quadrant Classification
        if overall_yield >= self.yield_threshold:
            if relevance_rate >= self.relevance_threshold:
                quadrant = "golden_source"
            else:
                quadrant = "needle_in_a_haystack"
        else:
            if relevance_rate >= self.relevance_threshold:
                quadrant = "filtering_burden"
            else:
                quadrant = "dead_weight"

        return quadrant, flags
