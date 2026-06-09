import asyncio
import logging
import httpx
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

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
    - Retries up to `max_retries` attempts for transient failures (network, timeout, 5xx).
    - Immediately fails and does not retry for 4xx errors.
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
                error_class = "timeout_error"
                error_detail = str(e)
            except httpx.NetworkError as e:
                error_class = "network_error"
                error_detail = str(e)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if 400 <= status < 500:
                    return FetchResult(
                        status_code=status,
                        content=None,
                        etag=None,
                        last_modified=None,
                        error_class="http_error_4xx",
                        error_detail=f"HTTP {status}: {e.response.text[:200]}",
                        retry_count=retry_count
                    )
                else:
                    error_class = "http_error_5xx"
                    error_detail = f"HTTP {status}: {e.response.text[:200]}"
            except Exception as e:
                return FetchResult(
                    status_code=None,
                    content=None,
                    etag=None,
                    last_modified=None,
                    error_class="unexpected_error",
                    error_detail=f"{type(e).__name__}: {str(e)}",
                    retry_count=retry_count
                )

            if retry_count < max_retries:
                sleep_time = backoff_factor * (2 ** retry_count)
                logger.warning(
                    f"Transient error fetching {xml_url} ({error_class}). "
                    f"Retrying in {sleep_time}s... Error: {error_detail}"
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
