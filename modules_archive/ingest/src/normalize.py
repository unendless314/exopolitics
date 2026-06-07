import re
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
    # Collapse multiple whitespaces/newlines/tabs to a single space
    cleaned = re.sub(r"\s+", " ", title)
    return cleaned.strip()

def normalize_url(url: Any) -> Optional[str]:
    """
    Normalizes a canonical URL conservatively:
    - Trim whitespace
    - Lowercase scheme and host
    - Remove URL fragment
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
        # We only normalize http or https URLs
        if not parsed.scheme or parsed.scheme.lower() not in ("http", "https"):
            return url_str

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path

        # Normalize trailing slash from path
        if path and path != "/":
            if path.endswith("/"):
                path = path[:-1]
        elif not path:
            path = "/"

        # Reassemble the URL without fragment
        normalized = urllib.parse.urlunparse((
            scheme,
            netloc,
            path,
            parsed.params,
            parsed.query,
            ""  # Strip fragment
        ))
        return normalized
    except Exception:
        # Fallback to the original stripped string in case of parsing failures
        return url_str

def normalize_published_at(parsed_time: Optional[time.struct_time], raw_string: Optional[str] = None) -> Optional[str]:
    """
    Normalizes a timestamp to UTC ISO-8601 string: YYYY-MM-DDTHH:MM:SSZ.
    Prefers the parsed struct_time (from feedparser), with raw string parsing as a fallback.
    """
    # 1. Try converting parsed_time (struct_time) if present
    if parsed_time is not None:
        try:
            # Convert UTC struct_time to epoch timestamp, then to UTC datetime.
            # This is timezone-safe and avoids re-interpreting local wall-clock components.
            epoch = calendar.timegm(parsed_time)
            dt = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError, OverflowError):
            pass

    # 2. Try parsing raw string fallback
    if raw_string and isinstance(raw_string, str):
        cleaned_str = raw_string.strip()
        if cleaned_str:
            # Let's try standard formats
            for fmt in (
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %Z",  # RFC 822/1123
                "%a, %d %b %Y %H:%M:%S %z",
            ):
                try:
                    # Strip any trailing zone name or offset to keep simple or parse
                    dt = datetime.datetime.strptime(cleaned_str, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    else:
                        dt = dt.astimezone(datetime.timezone.utc)
                    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    continue

    return None
