import hashlib
from typing import List, Optional, Tuple

from .normalize import normalize_dedup_title

# Minimum normalized-title length required to emit a title-hash dedup marker.
# Very short titles ("Breaking News", "Live Updates") carry too little identity
# to be safe as a global cross-source identity signal.
MIN_TITLE_HASH_LENGTH = 12


def generate_dedup_keys(
    source_id: int,
    guid: Optional[str],
    canonical_url: Optional[str],
    title: str,
    published_at: Optional[str],
    summary: Optional[str] = None
) -> Tuple[str, str, List[Tuple[str, str]]]:
    """
    Implements rule precedence and deterministic key generation.

    Primary key precedence (stored on source_item.ingest_dedup_key):
        1. url  - normalized canonical URL (cross-source / global)
        2. guid - feed GUID (source-scoped)
        3. tp   - title + published timestamp heuristic (source-scoped)
        4. fh   - fallback hash from normalized item inputs (source-scoped)

    The URL rule ranks above GUID because a canonical URL identifies the same
    article across every source, while feed GUIDs are only unique within one
    feed's schema. guid/tp/fh stay source-scoped to prevent cross-source
    identity collisions from conflicting feed schemas.

    Extra markers (checked and stored in addition to the primary key):
        th - normalized title hash (cross-source / global). Emitted whenever
             the normalized title is at least MIN_TITLE_HASH_LENGTH characters.
             Catches syndicated copies of one article whose URLs differ
             (e.g. opaque Google News redirect URLs or tracking parameters).

    An item is treated as a duplicate when ANY of its keys (primary or extra)
    already exists in ingest_dedup_marker.

    Returns:
        Tuple[primary_dedup_key, primary_dedup_rule, extra_markers]
        where extra_markers is a list of (dedup_key, dedup_rule) tuples.
    """
    extra_markers: List[Tuple[str, str]] = []

    normalized_title = normalize_dedup_title(title)
    if len(normalized_title) >= MIN_TITLE_HASH_LENGTH:
        th = hashlib.md5(normalized_title.encode("utf-8")).hexdigest()
        extra_markers.append((f"th:{th}", "th"))

    # Rule 1: Normalized Canonical URL (Cross-source / Global)
    if canonical_url and isinstance(canonical_url, str) and canonical_url.strip():
        val = canonical_url.strip()
        key = f"url:{val}"
        return key, "url", extra_markers

    # Rule 2: Trusted Feed GUID (Source-scoped)
    if guid and isinstance(guid, str) and guid.strip():
        val = guid.strip()
        key = f"guid:{source_id}:{val}"
        return key, "guid", extra_markers

    # Rule 3: Title + Published Timestamp Heuristic (Source-scoped)
    if title and isinstance(title, str) and title.strip() and published_at:
        t_val = title.strip()
        p_val = published_at.strip()
        if t_val and p_val:
            combined = f"{t_val}|{p_val}"
            h = hashlib.md5(combined.encode("utf-8")).hexdigest()
            key = f"tp:{source_id}:{h}"
            return key, "tp", extra_markers

    # Rule 4: Source-scoped Fallback Hash
    inputs = [str(source_id)]
    if title and isinstance(title, str):
        inputs.append(title.strip())
    if canonical_url and isinstance(canonical_url, str):
        inputs.append(canonical_url.strip())
    if published_at and isinstance(published_at, str):
        inputs.append(published_at.strip())
    if summary and isinstance(summary, str):
        inputs.append(summary.strip())

    combined_fallback = "|".join(inputs)
    h_fallback = hashlib.md5(combined_fallback.encode("utf-8")).hexdigest()
    key = f"fh:{source_id}:{h_fallback}"
    return key, "fh", extra_markers
