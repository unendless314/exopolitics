import asyncio
import json
import logging
import os
import time
from typing import Dict, Any, List, Optional
import httpx

from .config import ClassifyConfig, ProviderConfig
from .prompt_loader import PromptTemplate
from .repository import ClassificationRepository, get_utc_now_iso8601, transaction

logger = logging.getLogger("classify.classifier")

class ClassifierError(Exception):
    pass

class NonRetryableClassifierError(ClassifierError):
    pass

class RetryableClassifierError(ClassifierError):
    pass

class AsyncRateLimiter:
    def __init__(self, rate_limit_per_minute: int):
        self.rate = rate_limit_per_minute
        self.interval = 60.0
        self.tokens = float(rate_limit_per_minute)
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(float(self.rate), self.tokens + elapsed * (self.rate / self.interval))
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                # Sleep until a token becomes available
                sleep_dur = (1.0 - self.tokens) * (self.interval / self.rate)
                await asyncio.sleep(sleep_dur)

def clean_json_text(text: str) -> str:
    """
    Cleans raw LLM text by stripping markdown block delimiters if present.
    """
    text = text.strip()
    if text.startswith("```"):
        first_line_end = text.find("\n")
        if first_line_end != -1:
            text = text[first_line_end:].strip()
        else:
            text = text[3:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text

def parse_and_validate_response(content: str) -> Dict[str, Any]:
    """
    Parses raw LLM text into JSON and validates according to classification policies.
    Raises json.JSONDecodeError on malformed JSON, and ValueError on schema/policy violations.
    """
    cleaned = clean_json_text(content)
    parsed = json.loads(cleaned)

    # 1. Missing Mandatory Keys
    required_keys = ["topic_class", "classification_confidence", "edit_candidate", "classification_reason"]
    for key in required_keys:
        if key not in parsed:
            raise ValueError(f"Missing mandatory key: '{key}'")

    # 2. Invalid Enum Value for topic_class
    topic_class = parsed["topic_class"]
    if topic_class not in ("core", "adjacent", "irrelevant", "unknown"):
        raise ValueError(f"Invalid topic_class: '{topic_class}'")

    # 3. Out-of-Bounds Confidence
    confidence = parsed["classification_confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("classification_confidence must be a numeric value")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"classification_confidence must be between 0.0 and 1.0, got: {confidence}")

    # 4. Invalid Edit Flag
    edit_candidate = parsed["edit_candidate"]
    if edit_candidate in (1, 1.0, True, "1", "true", "True"):
        edit_val = 1
    elif edit_candidate in (0, 0.0, False, "0", "false", "False"):
        edit_val = 0
    else:
        raise ValueError(f"Invalid edit_candidate: '{edit_candidate}'")

    # 5. Validate classification_reason is string
    reason = parsed["classification_reason"]
    if not isinstance(reason, str):
        raise ValueError("classification_reason must be a string")

    return {
        "topic_class": topic_class,
        "classification_confidence": float(confidence),
        "edit_candidate": edit_val,
        "classification_reason": reason.strip()
    }

async def call_llm(
    client: httpx.AsyncClient,
    config: ClassifyConfig,
    provider_config: ProviderConfig,
    system_instruction: str,
    user_prompt: str
) -> str:
    """
    Performs the HTTP post request to the LLM completions endpoint.
    Raises RetryableClassifierError or NonRetryableClassifierError based on outcome.
    """
    api_key = os.environ.get(provider_config.api_key_env)
    if not api_key:
        raise NonRetryableClassifierError(f"API key environment variable '{provider_config.api_key_env}' is not set.")
    
    api_base = provider_config.api_base or "https://api.openai.com/v1"
    url = f"{api_base.rstrip('/')}/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    body = {
        "model": provider_config.model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": config.request_defaults.temperature,
        "top_p": config.request_defaults.top_p,
        "max_tokens": config.request_defaults.max_output_tokens
    }
    
    if provider_config.supports_structured_output:
        body["response_format"] = {"type": "json_object"}
        
    try:
        response = await client.post(
            url,
            headers=headers,
            json=body,
            timeout=config.execution_policy.request_timeout_seconds
        )
    except httpx.TimeoutException as e:
        raise RetryableClassifierError(f"LLM request timed out after {config.execution_policy.request_timeout_seconds} seconds.") from e
    except httpx.RequestError as e:
        raise RetryableClassifierError(f"LLM request failed due to network error: {e}") from e
        
    if response.status_code == 429:
        raise RetryableClassifierError("LLM request rate limited (HTTP 429).")
    elif response.status_code in (500, 502, 503, 504):
        raise RetryableClassifierError(f"LLM provider outage (HTTP {response.status_code}).")
    elif response.status_code != 200:
        raise NonRetryableClassifierError(f"LLM request failed with HTTP {response.status_code}: {response.text}")
        
    try:
        resp_json = response.json()
        content = resp_json["choices"][0]["message"]["content"]
        return content
    except (KeyError, IndexError, ValueError) as e:
        raise RetryableClassifierError(f"Invalid API response structure: {e}")

async def process_single_item(
    item: Dict[str, Any],
    client: httpx.AsyncClient,
    config: ClassifyConfig,
    provider_config: ProviderConfig,
    template: PromptTemplate,
    repo: ClassificationRepository,
    sem: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    progress_callback
) -> Optional[Dict[str, Any]]:
    """
    Processes a single item: checks low-context, calls LLM with retries, and persists results.
    """
    source_item_id = item["source_item_id"]
    title = item["title"] or ""
    summary = item["summary"] or ""
    
    # 1. Deterministic Low-Context Check
    combined_len = len(title) + len(summary)
    min_len = config.execution_policy.min_context_characters
    
    if combined_len < min_len:
        # Low-Context Path (bypasses LLM, semaphore, rate-limiter)
        result = {
            "source_item_id": source_item_id,
            "topic_class": "unknown",
            "classification_confidence": None,
            "edit_candidate": 0,
            "classification_reason": f"Feed metadata length ({combined_len}) is below the minimum context threshold of {min_len} characters.",
            "model_name": config.deterministic_classification.model_name,
            "prompt_version": config.deterministic_classification.prompt_version,
            "classified_at": get_utc_now_iso8601(),
            "created_at": get_utc_now_iso8601()
        }
        try:
            with transaction(repo.conn):
                repo.save_classification_result(result)
            progress_callback(source_item_id, "low-context", None)
            return result
        except Exception as e:
            logger.error(f"Database error writing low-context result for item {source_item_id}: {e}")
            return None

    # 2. LLM Path (bounded concurrency & rate limit)
    retry_attempts = config.execution_policy.retry_attempts
    backoff_factor = config.execution_policy.backoff_factor
    
    user_prompt = template.render(title=title, summary=summary)
    system_instruction = template.system_instruction
    
    for attempt in range(retry_attempts + 1):
        try:
            async with sem:
                await rate_limiter.acquire()
                content = await call_llm(
                    client=client,
                    config=config,
                    provider_config=provider_config,
                    system_instruction=system_instruction,
                    user_prompt=user_prompt
                )
                
            validated = parse_and_validate_response(content)
            
            # Persist transactionally
            result = {
                "source_item_id": source_item_id,
                **validated,
                "model_name": provider_config.model_name,
                "prompt_version": template.version,
                "classified_at": get_utc_now_iso8601(),
                "created_at": get_utc_now_iso8601()
            }
            with transaction(repo.conn):
                repo.save_classification_result(result)
            progress_callback(source_item_id, "success", result["topic_class"])
            return result

        except NonRetryableClassifierError as e:
            logger.error(f"Non-retryable error for item {source_item_id}: {e}")
            # Do not retry, do not write database row
            break
        except Exception as e:
            # Determine specific error cause
            err_type = "ValidationError" if isinstance(e, ValueError) else type(e).__name__
            
            if attempt == retry_attempts:
                # Retries exhausted
                logger.error(f"Failed to classify item {source_item_id} after {retry_attempts} retries. Cause: {err_type} - {e}")
                # Do not write row, return None
                break
                
            backoff_delay = backoff_factor * (2 ** attempt)
            logger.warning(f"Retryable error for item {source_item_id} (attempt {attempt + 1}/{retry_attempts + 1}): {err_type} - {e}. Retrying in {backoff_delay}s...")
            await asyncio.sleep(backoff_delay)
            
    return None

async def classify_batch(
    items: List[Dict[str, Any]],
    config: ClassifyConfig,
    template: PromptTemplate,
    repo: ClassificationRepository,
    progress_callback
) -> List[Dict[str, Any]]:
    """
    Orchestrates execution of the batch concurrently.
    """
    if not items:
        return []

    provider_name = config.active_provider
    provider_config = config.providers.get(provider_name)
    if not provider_config:
        raise ValueError(f"Active provider '{provider_name}' is not configured in model settings.")

    sem = asyncio.Semaphore(config.execution_policy.max_concurrent_requests)
    rate_limiter = AsyncRateLimiter(config.execution_policy.rate_limit_per_minute)
    
    async with httpx.AsyncClient() as client:
        tasks = []
        for item in items:
            tasks.append(
                process_single_item(
                    item=item,
                    client=client,
                    config=config,
                    provider_config=provider_config,
                    template=template,
                    repo=repo,
                    sem=sem,
                    rate_limiter=rate_limiter,
                    progress_callback=progress_callback
                )
            )
        
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]
