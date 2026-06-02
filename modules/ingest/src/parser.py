import feedparser
from typing import List, Optional
from .models import NormalizedItem
from .normalize import normalize_title, normalize_url, normalize_published_at
from .dedup import generate_dedup_key_and_rule

def parse_feed_entries(
    source_id: int,
    xml_content: bytes,
    fetched_at: str
) -> List[NormalizedItem]:
    """
    Parses raw XML feed content using feedparser, normalizes all elements, 
    computes deduplication keys, and returns a list of NormalizedItem dataclasses.
    """
    # Feedparser parses bytes or string, but passing bytes is safer and respects encoding headers
    parsed = feedparser.parse(xml_content)
    normalized_items: List[NormalizedItem] = []

    for entry in parsed.entries:
        # Extract title and normalize
        title_raw = entry.get("title", "")
        title = normalize_title(title_raw)

        # Extract GUID
        guid = entry.get("id") or entry.get("guid")
        if guid:
            guid = str(guid).strip()

        # Extract canonical URL and normalize
        link = entry.get("link")
        canonical_url = normalize_url(link)

        # Extract summary/description
        summary = entry.get("summary") or entry.get("description")
        if summary:
            summary = str(summary).strip()

        # Extract and normalize published timestamp
        published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        published_raw = entry.get("published") or entry.get("updated")
        published_at = normalize_published_at(published_parsed, published_raw)

        # Generate deduplication key & rule
        dedup_key, dedup_rule = generate_dedup_key_and_rule(
            source_id=source_id,
            guid=guid,
            canonical_url=canonical_url,
            title=title,
            published_at=published_at,
            summary=summary
        )

        normalized_items.append(
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
            )
        )

    return normalized_items
