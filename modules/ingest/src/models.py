from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple

@dataclass(frozen=True)
class NormalizedItem:
    source_id: int
    source_item_guid: Optional[str]
    canonical_url: Optional[str]
    title: str
    summary: Optional[str]
    published_at: Optional[str]  # UTC ISO-8601 YYYY-MM-DDTHH:MM:SSZ
    fetched_at: str              # UTC ISO-8601 YYYY-MM-DDTHH:MM:SSZ
    ingest_dedup_key: str        # Prefix encoded primary dedup key
    dedup_rule: str              # 'guid', 'url', 'tp', 'fh'
    extra_dedup_markers: Tuple[Tuple[str, str], ...] = ()  # additional global markers, e.g. ('th:<hash>', 'th')
