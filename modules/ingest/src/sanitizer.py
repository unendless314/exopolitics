import html
import re
from typing import Dict, List, Any, Optional, Tuple
from bs4 import BeautifulSoup
from .config import SanitizationProfile

# Centralized low-context threshold constants (easily tunable)
MIN_TEXT_LENGTH = 100
MAX_TITLE_OVERLAP_REMAINING = 40
BOILERPLATE_THRESHOLD = 3
BOILERPLATE_KEYWORDS = [
    "read more", "click here", "all rights reserved", "subscribe to",
    "share on facebook", "share on twitter", "privacy policy", "terms of service",
    "cookie policy", "newsletter", "follow us on", "comment below"
]

def detect_html_markup(text: str) -> bool:
    """
    Returns True if the text contains HTML tags or common HTML entities.
    """
    if not text:
        return False
    if not ("<" in text and ">" in text) and not ("&" in text and ";" in text):
        return False
    # Check for HTML entities using a regex (e.g., &amp;, &#123;)
    if re.search(r"&[a-zA-Z0-9#]+;", text):
        return True
    try:
        soup = BeautifulSoup(text, "html.parser")
        return bool(soup.find())
    except Exception:
        return False


def extract_raw_candidate(entry: Dict[str, Any], input_preference: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts the raw text body candidate based on the input preference list.
    Returns a tuple of (raw_text, selected_field).
    """
    for field in input_preference:
        val = entry.get(field)
        if not val:
            continue
        
        # If it's a list (like content field from feedparser)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and "value" in item and item["value"]:
                    return item["value"], field
                elif isinstance(item, str) and item:
                    return item, field
        elif isinstance(val, str) and val.strip():
            return val, field
            
    return None, None


def sanitize_item(
    entry: Dict[str, Any],
    normalized_title: str,
    profile: SanitizationProfile,
    method_label: str = "bs4_default"
) -> Dict[str, Any]:
    """
    Runs the raw feed entry through the sanitization pipeline.
    
    Pipeline Stages:
    1. Select raw text candidate using input preference.
    2. Detect HTML.
    3. Remove scripts, styles, footer, nav, and other remove_selectors.
    4. Extract content if content_selectors are set.
    5. Decode HTML entities.
    6. Normalize whitespace and collapse blank lines.
    7. Truncate if max_length is exceeded.
    8. Run low-context detection checks.
    
    Returns a dictionary of sanitization outputs ready for database insertion.
    """
    raw_payload, selected_field = extract_raw_candidate(entry, profile.input_preference)
    
    if not raw_payload:
        # No body candidate existed
        return {
            "sanitized_text": "",
            "html_detected": False,
            "was_truncated": False,
            "is_low_context": True,
            "low_context_reason": "missing_body",
            "raw_text_length": 0,
            "sanitized_text_length": 0,
            "reduction_ratio": 0.0,
            "sanitization_method": method_label
        }

    raw_len = len(raw_payload)
    html_detected = detect_html_markup(raw_payload)

    # BeautifulSoup processing
    # Even if html is not explicitly detected, parse with bs4 to handle any unescaped tags or entities safely
    soup = BeautifulSoup(raw_payload, "html.parser")

    # Count link text vs total text in original soup to detect mostly_links
    original_text = soup.get_text()
    original_text_len = len(original_text.strip())
    
    link_text_len = 0
    for a in soup.find_all("a"):
        link_text_len += len(a.get_text())

    # Default remove selectors
    default_remove = ["script", "style", "nav", "footer"]
    for tag in default_remove:
        for element in soup.find_all(tag):
            element.decompose()

    # Custom remove selectors
    for selector in profile.remove_selectors:
        try:
            for element in soup.select(selector):
                element.decompose()
        except Exception:
            # Skip invalid css selector
            pass

    # Extract content
    extracted_text = ""
    if profile.content_selectors:
        matched_elements = []
        for selector in profile.content_selectors:
            try:
                matched_elements.extend(soup.select(selector))
            except Exception:
                pass
        
        if matched_elements:
            extracted_text = "\n".join([el.get_text() for el in matched_elements])
        else:
            # Content selectors were specified but nothing matched
            extracted_text = ""
    else:
        extracted_text = soup.get_text()

    # Decode entities if enabled
    if profile.decode_entities:
        extracted_text = html.unescape(extracted_text)

    # Whitespace normalization
    lines = extracted_text.splitlines()
    normalized_lines = []
    
    for line in lines:
        if profile.normalize_whitespace:
            line = re.sub(r"\s+", " ", line).strip()
        normalized_lines.append(line)

    if profile.collapse_blank_lines:
        collapsed_lines = []
        prev_empty = False
        for line in normalized_lines:
            if not line:
                if not prev_empty:
                    collapsed_lines.append(line)
                    prev_empty = True
            else:
                collapsed_lines.append(line)
                prev_empty = False
        normalized_lines = collapsed_lines

    sanitized_text = "\n".join(normalized_lines).strip()

    # Truncation
    was_truncated = False
    if profile.max_length and len(sanitized_text) > profile.max_length:
        sanitized_text = sanitized_text[:profile.max_length]
        was_truncated = True

    sanitized_len = len(sanitized_text)
    
    # Calculate reduction ratio
    reduction_ratio = None
    if raw_len > 0:
        reduction_ratio = float(sanitized_len) / float(raw_len)

    # Low-context checks
    is_low_context = False
    low_context_reason = None

    trimmed_title = normalized_title.strip().lower()
    trimmed_sanitized = sanitized_text.lower()

    # 1. Post cleanup empty
    if not sanitized_text:
        is_low_context = True
        low_context_reason = "post_cleanup_empty"

    # 2. Title only (exact match)
    elif trimmed_sanitized == trimmed_title:
        is_low_context = True
        low_context_reason = "title_only"

    # 3. Title heavy (sanitized text is dominated by the title)
    elif trimmed_title in trimmed_sanitized and len(trimmed_sanitized.replace(trimmed_title, "").strip()) < MAX_TITLE_OVERLAP_REMAINING:
        is_low_context = True
        low_context_reason = "title_heavy"

    # 4. Mostly links
    elif original_text_len > 0 and (float(link_text_len) / float(original_text_len)) > 0.7:
        is_low_context = True
        low_context_reason = "mostly_links"

    # 5. Template heavy (boilerplate keywords)
    elif any(kw in trimmed_sanitized for kw in BOILERPLATE_KEYWORDS) and sum(1 for kw in BOILERPLATE_KEYWORDS if kw in trimmed_sanitized) >= BOILERPLATE_THRESHOLD:
        is_low_context = True
        low_context_reason = "template_heavy"

    # 6. Too short (generic fallback length check)
    elif sanitized_len < MIN_TEXT_LENGTH:
        is_low_context = True
        low_context_reason = "truncated_to_low_context" if was_truncated else "too_short"

    return {
        "sanitized_text": sanitized_text,
        "html_detected": html_detected,
        "was_truncated": was_truncated,
        "is_low_context": is_low_context,
        "low_context_reason": low_context_reason,
        "raw_text_length": raw_len,
        "sanitized_text_length": sanitized_len,
        "reduction_ratio": reduction_ratio,
        "sanitization_method": method_label,
        "raw_payload": raw_payload
    }
