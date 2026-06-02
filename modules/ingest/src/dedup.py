import hashlib
from typing import Optional, Tuple
from .models import NormalizedItem

def generate_dedup_key_and_rule(
    source_id: int,
    guid: Optional[str],
    canonical_url: Optional[str],
    title: str,
    published_at: Optional[str],
    summary: Optional[str] = None
) -> Tuple[str, str]:
    """
    Implements rule precedence and deterministic key generation (DEDUP_POLICY.md).
    Only URL matching allows cross-source deduplication.
    All other rules (guid, tp, fh) are source-scoped by encoding source_id into the key.

    Returns:
        Tuple[dedup_key, dedup_rule]
    """
    # Rule 1: Trusted Feed GUID (Source-scoped)
    if guid and isinstance(guid, str) and guid.strip():
        val = guid.strip()
        # Format: guid:<source_id>:<guid_val>
        key = f"guid:{source_id}:{val}"
        return key, "guid"

    # Rule 2: Normalized Canonical URL (Cross-source / Global)
    if canonical_url and isinstance(canonical_url, str) and canonical_url.strip():
        val = canonical_url.strip()
        # Format: url:<normalized_url>
        key = f"url:{val}"
        return key, "url"

    # Rule 3: Title + Published Timestamp Heuristic (Source-scoped)
    if title and isinstance(title, str) and title.strip() and published_at:
        t_val = title.strip()
        p_val = published_at.strip()
        if t_val and p_val:
            # We hash the combination of title and timestamp to produce a clean, fixed-size key
            combined = f"{t_val}|{p_val}"
            h = hashlib.md5(combined.encode("utf-8")).hexdigest()
            key = f"tp:{source_id}:{h}"
            return key, "tp"

    # Rule 4: Source-scoped Fallback Hash
    # Combine best available normalized fields
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
    return key, "fh"
