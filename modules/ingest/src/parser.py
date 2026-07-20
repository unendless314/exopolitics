import feedparser
from typing import List, Optional, Dict, Any, Tuple
from .models import NormalizedItem
from .normalize import normalize_title, normalize_url, normalize_published_at
from .dedup import generate_dedup_key_and_rule

def parse_feed_entries(
    source_id: int,
    xml_content: bytes,
    fetched_at: str
) -> List[Tuple[NormalizedItem, Dict[str, Any]]]:
    """
    Parses raw XML feed content using feedparser, normalizes all elements, 
    computes deduplication keys, and returns a list of (NormalizedItem, raw_entry) tuples.
    """
    parsed = feedparser.parse(xml_content)
    normalized_items: List[Tuple[NormalizedItem, Dict[str, Any]]] = []

    for entry in parsed.entries:
        title_raw = entry.get("title", "")
        title = normalize_title(title_raw)

        guid = entry.get("id") or entry.get("guid")
        if guid:
            guid = str(guid).strip()

        link = entry.get("link")
        if not link:
            links = entry.get("links", [])
            for l in links:
                if isinstance(l, dict) and l.get("rel") == "enclosure":
                    link = l.get("href")
                    break
        canonical_url = normalize_url(link)

        summary = entry.get("summary") or entry.get("description")
        if summary:
            summary = str(summary).strip()

        # If summary is missing but content exists, try to get summary/body from content
        if not summary:
            content_list = entry.get("content")
            if isinstance(content_list, list) and content_list:
                first_content = content_list[0]
                if isinstance(first_content, dict) and "value" in first_content:
                    summary = str(first_content["value"]).strip()
                elif isinstance(first_content, str):
                    summary = first_content.strip()

        published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        published_raw = entry.get("published") or entry.get("updated")
        published_at = normalize_published_at(published_parsed, published_raw)

        dedup_key, dedup_rule = generate_dedup_key_and_rule(
            source_id=source_id,
            guid=guid,
            canonical_url=canonical_url,
            title=title,
            published_at=published_at,
            summary=summary
        )

        normalized_items.append(
            (
                NormalizedItem(
                    source_id=source_id,
                    source_item_guid=guid,
                    canonical_url=canonical_url,
                    title=title,
                    summary=summary,
                    published_at=published_at,
                    fetched_at=fetched_at,
                    ingest_dedup_key=dedup_key,
                    dedup_rule=dedup_rule
                ),
                entry
            )
        )

    return normalized_items
