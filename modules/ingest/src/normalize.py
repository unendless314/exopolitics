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
