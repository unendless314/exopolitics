import asyncio
import datetime
import logging
import math
import httpx
from email.utils import parsedate_to_datetime
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

from .errors import ErrorClass

logger = logging.getLogger("ingest.fetcher")

@dataclass(frozen=True)
class FetchResult:
    status_code: Optional[int]
    content: Optional[bytes]
    etag: Optional[str]
    last_modified: Optional[str]
    error_class: Optional[str]  # 'network_error', 'timeout_error', 'http_error_4xx', 'http_error_5xx', 'unexpected_error'
    error_detail: Optional[str]
    retry_count: int

def _parse_retry_delay(header_value: Optional[str]) -> Optional[float]:
    """
    Parses a rate-limit header value (`Retry-After` or `x-ratelimit-reset`) into
    delay seconds. Accepts delay-seconds or an HTTP-date (RFC 7231). Returns
    None when the value is missing, unparseable, or non-finite (NaN/Infinity).
    """
    if not header_value:
        return None
    try:
        delay = float(header_value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(header_value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        delay = (parsed - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    if not math.isfinite(delay):
        return None
    return delay

async def fetch_feed(
    xml_url: str,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    timeout_seconds: float = 10.0,
    custom_headers: Optional[Dict[str, str]] = None,
    max_retries: int = 2,
    backoff_factor: float = 1.0,
    semaphore: Optional[asyncio.Semaphore] = None
) -> FetchResult:
    """
    Asynchronously fetches a remote XML feed with cache support and transient error retries.
    Enforces bounded concurrency via the provided semaphore.
    
    Retry Policy:
    - Retries up to `max_retries` attempts for transient failures (network, timeout, 5xx,
      and HTTP 429 rate limiting, which honors `retry-after` / `x-ratelimit-reset`
      headers clamped to 1-60s).
    - Immediately fails and does not retry for other 4xx errors.
    - 429 failures are classified as `http_error_4xx`; the precise cause remains
      available via `status_code` / persisted `http_status`.
    """
    sem_context = semaphore if semaphore is not None else asyncio.Semaphore(5)

    headers = {}
    if custom_headers:
        headers.update(custom_headers)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    retry_count = 0
    async with httpx.AsyncClient(follow_redirects=True) as client:
        while True:
            suggested_delay: Optional[float] = None
            try:
                logger.debug(f"Fetching {xml_url} (attempt {retry_count + 1})")
                
                async with sem_context:
                    response = await client.get(xml_url, headers=headers, timeout=timeout_seconds)
                
                status = response.status_code
                if status == 304:
                    return FetchResult(
                        status_code=304,
                        content=None,
                        etag=etag,
                        last_modified=last_modified,
                        error_class=None,
                        error_detail=None,
                        retry_count=retry_count
                    )
                
                response.raise_for_status()

                new_etag = response.headers.get("etag")
                new_last_modified = response.headers.get("last-modified")

                return FetchResult(
                    status_code=status,
                    content=response.content,
                    etag=new_etag,
                    last_modified=new_last_modified,
                    error_class=None,
                    error_detail=None,
                    retry_count=retry_count
                )

            except httpx.TimeoutException as e:
                error_class = ErrorClass.TIMEOUT.value
                error_detail = str(e)
            except httpx.NetworkError as e:
                error_class = ErrorClass.NETWORK.value
                error_detail = str(e)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 429:
                    # Retriable rate limit; classified as http_error_4xx with the
                    # precise cause kept in status_code/http_status. x-ratelimit-reset
                    # assumes Reddit semantics (seconds until reset); epoch-style
                    # values are bounded by the 60s cap.
                    error_class = ErrorClass.HTTP_4XX.value
                    error_detail = f"HTTP {status}: {e.response.text[:200]}"
                    delay = _parse_retry_delay(e.response.headers.get("retry-after"))
                    if delay is None:
                        delay = _parse_retry_delay(e.response.headers.get("x-ratelimit-reset"))
                    if delay is not None:
                        suggested_delay = min(max(delay, 1.0), 60.0)
                elif 400 <= status < 500:
                    return FetchResult(
                        status_code=status,
                        content=None,
                        etag=None,
                        last_modified=None,
                        error_class=ErrorClass.HTTP_4XX.value,
                        error_detail=f"HTTP {status}: {e.response.text[:200]}",
                        retry_count=retry_count
                    )
                else:
                    error_class = ErrorClass.HTTP_5XX.value
                    error_detail = f"HTTP {status}: {e.response.text[:200]}"
            except Exception as e:
                return FetchResult(
                    status_code=None,
                    content=None,
                    etag=None,
                    last_modified=None,
                    error_class=ErrorClass.UNEXPECTED.value,
                    error_detail=f"{type(e).__name__}: {str(e)}",
                    retry_count=retry_count
                )

            if retry_count < max_retries:
                if suggested_delay is not None:
                    sleep_time = suggested_delay
                else:
                    sleep_time = backoff_factor * (2 ** retry_count)

                logger.warning(
                    f"Transient error fetching {xml_url} ({error_class}). "
                    f"Retrying in {sleep_time:.1f}s... Error: {error_detail}"
                )
                retry_count += 1
                await asyncio.sleep(sleep_time)
            else:
                return FetchResult(
                    status_code=None if "HTTP" not in error_detail else int(error_detail.split()[1].replace(":", "")),
                    content=None,
                    etag=None,
                    last_modified=None,
                    error_class=error_class,
                    error_detail=error_detail,
                    retry_count=retry_count
                )
