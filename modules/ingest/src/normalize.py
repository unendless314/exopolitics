import re
import unicodedata
import urllib.parse
import datetime
import time
import calendar
from typing import Optional, Any

def normalize_title(title: Any) -> str:
    """
    Normalizes title by trimming leading/trailing whitespace
    and collapsing internal sequential whitespaces into a single space.
    """
    if not title or not isinstance(title, str):
        return ""
    cleaned = re.sub(r"\s+", " ", title)
    return cleaned.strip()

# Typographic punctuation folded to ASCII equivalents for dedup hashing
# (NFKC alone does not fold curly quotes or dashes).
_DEDUP_PUNCT_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
})

def normalize_dedup_title(title: Any) -> str:
    """
    Normalizes a title for cross-source dedup hashing (not for display):
    - Unicode NFKC normalization and casefolding (folds full-width chars, case)
    - Fold common typographic quotes/dashes to ASCII equivalents
    - Collapse all whitespace to single spaces and trim
    The Google News " - Publisher" suffix is intentionally retained: stripping it
    would merge genuinely different same-headline articles from different outlets.
    Returns "" for missing or non-string input.
    """
    if not title or not isinstance(title, str):
        return ""
    cleaned = unicodedata.normalize("NFKC", title).casefold()
    cleaned = cleaned.translate(_DEDUP_PUNCT_TRANSLATION)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()

# Well-known click-tracking / campaign query parameters that do not change
# article identity. Stripped during URL normalization so the same article
# reached via different tracking links still dedups to one canonical URL.
TRACKING_QUERY_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid",
})

def _is_tracking_param(param_name: str) -> bool:
    name = param_name.lower()
    return name.startswith("utm_") or name in TRACKING_QUERY_PARAMS

def normalize_url(url: Any) -> Optional[str]:
    """
    Normalizes a canonical URL conservatively:
    - Trim whitespace
    - Lowercase scheme and host
    - Remove URL fragment
    - Remove well-known tracking query parameters (utm_*, fbclid, gclid, ...)
    - Normalize trailing slash
    - Treat empty URL as None (do not invent one)
    """
    if not url or not isinstance(url, str):
        return None
    url_str = url.strip()
    if not url_str:
        return None

    try:
        parsed = urllib.parse.urlparse(url_str)
        if not parsed.scheme or parsed.scheme.lower() not in ("http", "https"):
            return url_str

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path

        if path and path != "/":
            if path.endswith("/"):
                path = path[:-1]
        elif not path:
            path = "/"

        query = parsed.query
        if query:
            kept = [
                (k, v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)
                if not _is_tracking_param(k)
            ]
            query = urllib.parse.urlencode(kept)

        normalized = urllib.parse.urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            query,
            ""  # Strip fragment
        ))
        return normalized
    except Exception:
        return url_str

def normalize_published_at(parsed_time: Optional[time.struct_time], raw_string: Optional[str] = None) -> Optional[str]:
    """
    Normalizes a timestamp to UTC ISO-8601 string: YYYY-MM-DDTHH:MM:SSZ.
    Prefers the parsed struct_time (from feedparser), with raw string parsing as a fallback.
    """
    if parsed_time is not None:
        try:
            epoch = calendar.timegm(parsed_time)
            dt = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError, OverflowError):
            pass

    if raw_string and isinstance(raw_string, str):
        cleaned_str = raw_string.strip()
        if cleaned_str:
            for fmt in (
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %Z",
                "%a, %d %b %Y %H:%M:%S %z",
            ):
                try:
                    dt = datetime.datetime.strptime(cleaned_str, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    else:
                        dt = dt.astimezone(datetime.timezone.utc)
                    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    continue
            
            # Simple ISO-8601 parsing fallback for string formats like 2026-06-09T15:17:10+08:00
            try:
                dt = datetime.datetime.fromisoformat(cleaned_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                else:
                    dt = dt.astimezone(datetime.timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                pass

    return None
