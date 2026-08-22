"""
Payload validation, slug generation, UI label checks and payload assembly.

Everything here is deterministic and free of clock,
database and filesystem access; the orchestrator namespace re-exports the
public symbols for compatibility.
"""
import json
import re
from typing import Dict, Any, Set, Optional


class ValidationError(Exception):
    """Custom exception raised when artifact validation fails."""
    pass

def slugify(text: str) -> str:
    """
    Generate a URL-safe, lowercase slug from a string.
    """
    import unicodedata
    # Normalize unicode to ASCII representation
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    text = text.lower()
    # Replace non-alphanumeric character sequences with hyphens
    text = re.sub(r'[^a-z0-9\-]+', '-', text)
    # Collapse consecutive hyphens
    text = re.sub(r'-+', '-', text)
    # Strip leading and trailing hyphens
    text = text.strip('-')
    return text

def generate_slug(title: str, existing_slugs: Set[str]) -> str:
    """
    Generate a unique slug deterministically by appending a counter suffix on collision.
    """
    base_slug = slugify(title)
    if not base_slug:
        base_slug = "item"

    slug = base_slug
    counter = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug

# Presentation labels that must never leak into exported content values:
# the three English labels plus every zh/ja variant observed in
# known_issues/resolved/TRANSLATION_LABEL_LEAKAGE.md section 4.2.
UI_LABELS = (
    "Key Claim",
    "Evidence Level",
    "Objective Impact",
    # zh variants (section 4.2)
    "主要主張",
    "關鍵主張",
    "核心主張",
    "證據層級",
    "證據等級",
    "客觀影響",
    "實際影響",
    # ja variants (section 4.2)
    "主要な主張",
    "主張の要点",
    "証拠の水準",
    "証拠レベル",
    "証拠水準",
    "エビデンスレベル",
    "客観的な影響",
    "客観的影響",
    "目的上の影響",
)

_UI_LABEL_PREFIX_RE = re.compile(
    r"^[\s*_-]*(" + "|".join(UI_LABELS) + r")[\s*_]*[:：]"
)

# Semantic bullets key mapping, established exactly once here in publish.
# No other module assigns these keys.
BULLET_KEY_MAP = (
    ("bullet_1", "key_claim"),
    ("bullet_2", "evidence_level"),
    ("bullet_3", "objective_impact"),
)

def has_ui_label_prefix(value: str) -> bool:
    """True when a content value starts with one of the presentation UI labels."""
    return bool(_UI_LABEL_PREFIX_RE.match(value))

def validate_item_payload(payload: Dict[str, Any]) -> None:
    """
    Validates that an assembled export item payload conforms to the publish
    data contract. Aborts execution by raising ValidationError if any rule
    is violated.
    """
    display_title = payload.get("display_title")
    language_code = payload.get("language_code")
    slug = payload.get("slug")
    summary_short = payload.get("summary_short")
    downstream_action = payload.get("downstream_action")
    author_metadata = payload.get("author_metadata")

    if not display_title or not display_title.strip():
        raise ValidationError("display_title must be non-empty")
    if not language_code or not language_code.strip():
        raise ValidationError("language_code must be present")
    if not slug or not slug.strip():
        raise ValidationError("slug must be present")

    if not isinstance(summary_short, str) or not summary_short.strip():
        raise ValidationError("summary_short must be a string that remains non-empty after trimming")
    if has_ui_label_prefix(summary_short):
        raise ValidationError("summary_short must not start with a presentation UI label prefix")

    if downstream_action not in ("publish_summary", "publish_link"):
        raise ValidationError(f"invalid downstream_action: '{downstream_action}'")

    if "bullets" not in payload:
        raise ValidationError("bullets is required and must never be omitted")
    bullets = payload["bullets"]
    if downstream_action == "publish_link":
        if bullets is not None:
            raise ValidationError("bullets must be null when downstream_action is 'publish_link'")
    else:
        if not isinstance(bullets, dict):
            raise ValidationError("bullets must be an object when downstream_action is 'publish_summary'")
        expected_bullet_keys = {"key_claim", "evidence_level", "objective_impact"}
        if set(bullets.keys()) != expected_bullet_keys:
            raise ValidationError("bullets must contain exactly the keys 'key_claim', 'evidence_level', and 'objective_impact'")
        for key in sorted(expected_bullet_keys):
            value = bullets[key]
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"bullets.{key} must be a string that remains non-empty after trimming")
            if has_ui_label_prefix(value):
                raise ValidationError(f"bullets.{key} must not start with a presentation UI label prefix")

    # Author metadata validation (already parsed from JSON at assembly time)
    if not isinstance(author_metadata, dict):
        raise ValidationError("author_metadata must parse to a JSON object")

    if "source_module" not in author_metadata:
        raise ValidationError("author_metadata is missing required key: 'source_module'")
    if "writer_type" not in author_metadata:
        raise ValidationError("author_metadata is missing required key: 'writer_type'")

    source_module = author_metadata.get("source_module")
    if not isinstance(source_module, str) or not source_module.strip():
        raise ValidationError("author_metadata.source_module must be a string that remains non-empty after trimming")

    writer_type = author_metadata.get("writer_type")
    if writer_type in ("human", "hybrid"):
        editor = author_metadata.get("editor")
        if not isinstance(editor, str) or not editor.strip():
            raise ValidationError(f"editor field is required and must be non-empty when writer_type is '{writer_type}'")
    elif writer_type not in ("AI", "machine"):
        raise ValidationError(f"invalid writer_type: '{writer_type}'")

def get_disclosure_note(author_metadata: Dict[str, Any]) -> str:
    """
    Get the disclosure note based on writer_type from parsed author_metadata.
    Malformed metadata falls back to the AI-generated note; validate_item_payload
    rejects such payloads separately.
    """
    writer_type = author_metadata.get("writer_type") if isinstance(author_metadata, dict) else None
    if writer_type in ("human", "hybrid"):
        return "This item is AI-assisted and human-curated."
    else:
        return "This item is AI-generated."

def assemble_item_payload(payload_row: Dict[str, Any], slug: str, published_at: Optional[str]) -> Dict[str, Any]:
    """
    Assemble the export item payload from a canonical upstream row.
    Parses author_metadata and establishes the semantic bullets key mapping
    exactly once, here in publish; no other module assigns these keys.
    """
    author_metadata_str = payload_row.get("author_metadata")
    if author_metadata_str is None:
        raise ValidationError("author_metadata is required and cannot be NULL")
    try:
        author_metadata = json.loads(author_metadata_str)
    except Exception as e:
        raise ValidationError(f"author_metadata is invalid JSON: {str(e)}")

    downstream_action = payload_row.get("downstream_action")
    if downstream_action == "publish_summary":
        bullets = {key: payload_row.get(column) for column, key in BULLET_KEY_MAP}
    else:
        bullets = None

    return {
        "source_item_id": payload_row["source_item_id"],
        "language_code": payload_row["language_code"],
        "slug": slug,
        "display_title": payload_row["display_title"],
        "summary_short": payload_row["summary_short"],
        "bullets": bullets,
        "canonical_url": payload_row["canonical_url"],
        "source_published_at": payload_row["source_published_at"],
        "approved_at": payload_row["approved_at"],
        "published_at": published_at,
        "downstream_action": downstream_action,
        "disclosure_note": get_disclosure_note(author_metadata),
        "author_metadata": author_metadata
    }
