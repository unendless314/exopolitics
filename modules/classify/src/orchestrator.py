import asyncio
import json
import os
import pathlib
import random
from typing import Dict, Any, Optional, Tuple
import httpx

class ModelRefusalError(Exception):
    """Raised when the LLM provider explicitly refuses to generate a classification."""
    pass

from .config import ClassifyConfig
from .database import (
    get_connection,
    transaction,
    ClassificationResultRepository,
)

# JSON Schema for structured outputs definition
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "topic_class": {
            "type": "string",
            "enum": ["core", "adjacent", "irrelevant", "unknown"]
        },
        "classification_confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0
        },
        "classification_reason": {
            "type": "string",
            "maxLength": 300
        },
        "content_density": {
            "type": "string",
            "enum": ["low", "medium", "high"]
        },
        "source_text_quality": {
            "type": "string",
            "enum": ["poor", "usable", "strong"]
        },
        "primary_language_code": {
            "type": "string"
        },
        "governmental_involvement": {
            "type": "integer",
            "enum": [0, 1]
        },
        # Experimental signals allowlisted for sandbox research
        "content_timeliness": {
            "type": "string",
            "enum": ["current", "evergreen", "historical", "unclear"]
        },
        "primary_evidence_type": {
            "type": "string",
            "enum": ["physical_material", "radar_sensor", "video_photo", "eyewitness", "official_document", "scientific_paper", "media_report", "none"]
        }
    },
    "required": [
        "topic_class", 
        "classification_confidence", 
        "classification_reason", 
        "content_density", 
        "source_text_quality", 
        "primary_language_code", 
        "governmental_involvement"
    ]
}

PREVIEW_SEPARATOR = "=" * 82


def _build_messages(config: ClassifyConfig, title: str, sanitized_text: str) -> Dict[str, str]:
    user_prompt = config.active_template.user_prompt_template.format(
        title=title,
        sanitized_text=sanitized_text,
    )
    return {
        "system_instruction": config.active_template.system_instruction,
        "user_prompt": user_prompt,
    }


def _build_request_payload(config: ClassifyConfig, title: str, sanitized_text: str) -> Dict[str, Any]:
    provider = config.active_provider
    defaults = config.request_defaults
    messages = _build_messages(config, title, sanitized_text)

    payload: Dict[str, Any] = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": messages["system_instruction"]},
            {"role": "user", "content": messages["user_prompt"]},
        ],
        "temperature": defaults.temperature,
        "top_p": defaults.top_p,
        "max_tokens": defaults.max_output_tokens,
    }

    if provider.supports_structured_output:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "classification_result",
                "strict": True,
                "schema": JSON_SCHEMA,
            },
        }
    else:
        payload["response_format"] = {"type": "json_object"}

    return payload


def _parse_response_content(response: httpx.Response) -> Dict[str, Any]:
    res_data = response.json()
    if "choices" not in res_data or not res_data["choices"]:
        raise ValueError("LLM API returned response with empty or missing choices list")

    choice = res_data["choices"][0]
    message = choice.get("message", {})

    if message.get("refusal"):
        raise ModelRefusalError(f"Model refused to classify: {message['refusal']}")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "LLM API returned content block that is not a non-empty string "
            f"(got type {type(content).__name__})"
        )

    return json.loads(content)


def _print_preview_items(config: ClassifyConfig, pending_items: list[Any]) -> None:
    print(PREVIEW_SEPARATOR)
    print(f"PREVIEW PROMPTS MODE: Listing {len(pending_items)} pending prompt payloads")
    print(PREVIEW_SEPARATOR)
    for idx, item in enumerate(pending_items, 1):
        print(f"[{idx}] Source Item ID: {item['source_item_id']}")
        print(f"    Title: {item['title']}")
        print(f"    Is Low Context: {item['is_low_context']}")
        if item["is_low_context"]:
            print("    Route: Deterministic Bypass")
        else:
            messages = _build_messages(
                config,
                title=item["title"],
                sanitized_text=item["sanitized_text"],
            )
            print("    Route: LLM Request")
            print("    ---------------------------------------------------------")
            print("    System Message:")
            print(messages["system_instruction"].strip())
            print("    User Message:")
            print(messages["user_prompt"].strip())
        print(PREVIEW_SEPARATOR)


def validate_classification_response(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Validates parsed JSON response against contract specifications.
    Extracts stable fields and allowlisted experimental signals.
    Raises ValueError on contract violation.
    """
    # 1. Check required fields
    for field in JSON_SCHEMA["required"]:
        if field not in data:
            raise ValueError(f"Missing required response field: {field}")

    # 2. Validate topic_class
    topic_class = data["topic_class"]
    allowed_topics = JSON_SCHEMA["properties"]["topic_class"]["enum"]
    if topic_class not in allowed_topics:
        raise ValueError(f"Invalid topic_class '{topic_class}', must be one of {allowed_topics}")

    # 3. Validate confidence
    confidence = data["classification_confidence"]
    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        raise ValueError(f"Invalid classification_confidence '{confidence}', must be a float between 0.0 and 1.0")

    # 4. Validate content_density
    density = data["content_density"]
    allowed_densities = JSON_SCHEMA["properties"]["content_density"]["enum"]
    if density not in allowed_densities:
        raise ValueError(f"Invalid content_density '{density}', must be one of {allowed_densities}")

    # 5. Validate source_text_quality
    quality = data["source_text_quality"]
    allowed_qualities = JSON_SCHEMA["properties"]["source_text_quality"]["enum"]
    if quality not in allowed_qualities:
        raise ValueError(f"Invalid source_text_quality '{quality}', must be one of {allowed_qualities}")

    # 6. Validate governmental_involvement
    gov = data["governmental_involvement"]
    if gov not in (0, 1):
        raise ValueError(f"Invalid governmental_involvement '{gov}', must be 0 or 1")

    # 7. Validate primary_language_code
    lang = data["primary_language_code"]
    if not isinstance(lang, str) or not lang.strip():
        raise ValueError("primary_language_code must be a non-empty string")

    # 8. Validate classification_reason length
    reason = data["classification_reason"]
    if not isinstance(reason, str) or len(reason) > 300:
        raise ValueError("classification_reason must be a string up to 300 characters")

    # Extract stable record values
    stable_record = {
        "topic_class": topic_class,
        "classification_confidence": float(confidence),
        "classification_reason": reason,
        "content_density": density,
        "source_text_quality": quality,
        "primary_language_code": lang,
        "governmental_involvement": int(gov)
    }

    # Extract allowed experimental signals (filter out any unauthorized fields)
    additional_signals = {}
    if "content_timeliness" in data:
        t_val = data["content_timeliness"]
        allowed_t = JSON_SCHEMA["properties"]["content_timeliness"]["enum"]
        if t_val in allowed_t:
            additional_signals["content_timeliness"] = t_val
        else:
            raise ValueError(f"Invalid content_timeliness '{t_val}', must be one of {allowed_t}")

    if "primary_evidence_type" in data:
        e_val = data["primary_evidence_type"]
        allowed_e = JSON_SCHEMA["properties"]["primary_evidence_type"]["enum"]
        if e_val in allowed_e:
            additional_signals["primary_evidence_type"] = e_val
        else:
            raise ValueError(f"Invalid primary_evidence_type '{e_val}', must be one of {allowed_e}")

    return stable_record, additional_signals


async def fetch_llm_classification(
    client: httpx.AsyncClient,
    config: ClassifyConfig,
    title: str,
    sanitized_text: str,
    api_key: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Submits prompt to OpenAI-compatible endpoint with exponential backoff retries.
    Returns validated stable fields and additional signals.
    """
    provider = config.active_provider
    policy = config.execution_policy

    # Setup headers and API base
    api_base = provider.api_base or "https://api.openai.com/v1"
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = _build_request_payload(config, title=title, sanitized_text=sanitized_text)

    # Request retry loop
    attempts = policy.retry_attempts
    backoff_factor = policy.backoff_factor

    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=policy.request_timeout_seconds
            )

            # Check for Rate Limit (429) or Server error (5xx)
            if response.status_code == 429 or (500 <= response.status_code < 600):
                response.raise_for_status()

            # For other non-200 HTTP statuses, raise error directly (do not retry 4xx errors other than 429)
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"LLM API returned client error status {response.status_code}",
                    request=response.request,
                    response=response
                )

            parsed_json = _parse_response_content(response)

            # Validate against schema contract
            return validate_classification_response(parsed_json)

        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            # Check if we have remaining attempts
            if attempt == attempts:
                raise RuntimeError(f"Classification request failed after {attempts} attempts. Last error: {exc}")

            # Exponential backoff with jitter
            sleep_duration = (backoff_factor ** attempt) + random.uniform(0.1, 1.0)
            print(f"  Attempt {attempt} failed ({exc}). Retrying in {sleep_duration:.2f} seconds...")
            await asyncio.sleep(sleep_duration)

    raise RuntimeError("Unreachable")


async def classify_item(
    repo: ClassificationResultRepository,
    client: httpx.AsyncClient,
    config: ClassifyConfig,
    item_id: int,
    title: str,
    sanitized_text: str,
    is_low_context: bool,
    low_context_reason: Optional[str],
    api_key: str,
    db_lock: asyncio.Lock,
    commit: bool = True
) -> bool:
    """
    Processes a single item: routing it deterministically if low-context, 
    otherwise fetching LLM classification and persisting results within an item-level transaction.
    Returns True if successfully processed, False otherwise.
    """
    try:
        if is_low_context:
            # Deterministic Low-Context Bypass
            record = {
                "source_item_id": item_id,
                "topic_class": "unknown",
                "classification_reason": "Deterministic bypass: Item flagged as low-context during ingestion.",
                "classification_confidence": None,
                "content_density": None,
                "source_text_quality": None,
                "primary_language_code": None,
                "governmental_involvement": None,
                "additional_signals": None,
                "model_name": config.deterministic.model_name,
                "prompt_version": config.deterministic.prompt_version
            }
        else:
            # Proceed to LLM Call
            stable, extra = await fetch_llm_classification(
                client=client,
                config=config,
                title=title,
                sanitized_text=sanitized_text,
                api_key=api_key
            )
            record = {
                "source_item_id": item_id,
                "topic_class": stable["topic_class"],
                "classification_reason": stable["classification_reason"],
                "classification_confidence": stable["classification_confidence"],
                "content_density": stable["content_density"],
                "source_text_quality": stable["source_text_quality"],
                "primary_language_code": stable["primary_language_code"],
                "governmental_involvement": stable["governmental_involvement"],
                "additional_signals": extra,
                "model_name": config.active_provider.model_name,
                "prompt_version": config.active_template.version
            }

        # Persist within its own isolated transaction protected by an asyncio.Lock
        async with db_lock:
            with transaction(repo.conn, commit=commit):
                repo.upsert(record)

        return True

    except Exception as exc:
        print(f"Error processing item ID {item_id}: {exc}")
        return False


async def orchestrate_run(
    config: ClassifyConfig,
    db_path: pathlib.Path,
    batch_size: Optional[int] = None,
    preview_prompts: bool = False,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Orchestrates the classification batch run.
    Queries unclassified items, handles bypass routing, limits concurrency & rate,
    and isolates transaction updates per item.
    """
    # Initialize connection
    conn = get_connection(db_path)
    repo = ClassificationResultRepository(conn)

    # Respect batch overrides
    run_batch_size = batch_size if batch_size is not None else config.execution_policy.batch_size

    try:
        # Load pending items
        pending_items = repo.get_pending_items(limit=run_batch_size)
        if not pending_items:
            return {
                "total_queried": 0,
                "processed_successfully": 0,
                "failures": 0,
                "status": "completed"
            }

        # Preview prompts mode
        if preview_prompts:
            _print_preview_items(config, pending_items)
            return {
                "total_queried": len(pending_items),
                "processed_successfully": 0,
                "previewed": len(pending_items),
                "failures": 0,
                "status": "preview"
            }

        # Setup API credentials if LLM call is needed
        api_key = ""
        has_non_bypass = any(not item["is_low_context"] for item in pending_items)
        if has_non_bypass:
            api_key_env = config.active_provider.api_key_env
            api_key = os.environ.get(api_key_env, "")
            if not api_key:
                raise ValueError(
                    f"Missing required API key environment variable '{api_key_env}' for active provider '{config.active_provider_name}'"
                )

        # Semaphores and Lock for SQLite writes
        semaphore = asyncio.Semaphore(config.execution_policy.max_concurrent_requests)
        db_lock = asyncio.Lock()
        
        # Calculate rate limit delays
        # 60 / rate_limit_per_minute = seconds_per_request
        rpm = config.execution_policy.rate_limit_per_minute
        request_delay = 60.0 / rpm if rpm > 0 else 0.0

        # Asynchronous HTTP client
        async with httpx.AsyncClient() as client:
            tasks = []
            
            async def worker(item, idx):
                # Apply rate limit delay stagger
                if idx > 0 and request_delay > 0:
                    await asyncio.sleep(idx * request_delay)

                async with semaphore:
                    # Execute item classification.
                    # Under dry_run, we process fully but do not commit the transaction.
                    success = await classify_item(
                        repo=repo,
                        client=client,
                        config=config,
                        item_id=item["source_item_id"],
                        title=item["title"],
                        sanitized_text=item["sanitized_text"],
                        is_low_context=bool(item["is_low_context"]),
                        low_context_reason=item["low_context_reason"],
                        api_key=api_key,
                        db_lock=db_lock,
                        commit=not dry_run
                    )
                    return success

            for idx, item in enumerate(pending_items):
                tasks.append(worker(item, idx))

            results = await asyncio.gather(*tasks)

        succeeded = sum(1 for r in results if r)
        failed = len(results) - succeeded

        return {
            "total_queried": len(pending_items),
            "processed_successfully": succeeded,
            "failures": failed,
            "status": "completed"
        }

    finally:
        conn.close()
