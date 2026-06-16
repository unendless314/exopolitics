import asyncio
import json
import os
import pathlib
import random
import sqlite3
import sys
from typing import Dict, Any, Optional, Tuple, List

import httpx

from .config import CurateConfig
from .database import (
    get_connection,
    transaction,
    CurationRepository,
    get_utc_now_iso8601
)

class ModelRefusalError(Exception):
    """Raised when the LLM provider explicitly refuses to generate a curation."""
    pass

class ProcessLock:
    def __init__(self, lock_path: pathlib.Path):
        self.lock_path = lock_path
        self.fp = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            try:
                self.fp = open(self.lock_path, 'w')
                import msvcrt
                msvcrt.locking(self.fp.fileno(), msvcrt.LK_NBLCK, 1)
            except (IOError, OSError, ImportError) as e:
                if self.fp:
                    self.fp.close()
                raise RuntimeError(f"Could not acquire lock on {self.lock_path}. Another process is running. ({e})")
        else:
            try:
                self.fp = open(self.lock_path, 'w')
                import fcntl
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError, ImportError) as e:
                if self.fp:
                    self.fp.close()
                raise RuntimeError(f"Could not acquire lock on {self.lock_path}. Another process is running. ({e})")

    def release(self) -> None:
        if self.fp:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.fp.seek(0)
                    msvcrt.locking(self.fp.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.fp.close()
            try:
                os.remove(self.lock_path)
            except Exception:
                pass


# JSON Schema for curation structured output
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "curation_decision": {
            "type": "object",
            "properties": {
                "curate_status": {
                    "type": "string",
                    "enum": ["approved", "rejected"]
                },
                "downstream_action": {
                    "type": "string",
                    "enum": ["publish_link", "publish_summary", "edit_rewrite", "reject_discard"]
                },
                "decision_reason": {
                    "type": "string",
                    "maxLength": 250
                }
            },
            "required": ["curate_status", "downstream_action", "decision_reason"],
            "additionalProperties": False
        },
        "editor_brief": {
            "type": ["object", "null"],
            "properties": {
                "brief_goal": {
                    "type": "string"
                },
                "target_format": {
                    "type": "string",
                    "enum": ["link_card", "structured_summary"]
                },
                "key_claim": {
                    "type": ["string", "null"]
                },
                "key_evidence": {
                    "type": ["string", "null"]
                },
                "required_context": {
                    "type": ["string", "null"]
                },
                "risk_flags": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                },
                "tone_guidance": {
                    "type": "string"
                }
            },
            "required": ["brief_goal", "target_format", "risk_flags", "tone_guidance"],
            "additionalProperties": False
        },
        "curation_output": {
            "type": ["object", "null"],
            "properties": {
                "display_title": {
                    "type": "string",
                    "maxLength": 250
                },
                "summary_short": {
                    "type": "string",
                    "maxLength": 500
                },
                "bullet_1": {
                    "type": ["string", "null"],
                    "maxLength": 250
                },
                "bullet_2": {
                    "type": ["string", "null"],
                    "maxLength": 250
                },
                "bullet_3": {
                    "type": ["string", "null"],
                    "maxLength": 250
                },
                "source_attribution_note": {
                    "type": ["string", "null"],
                    "maxLength": 250
                }
            },
            "required": ["display_title", "summary_short"],
            "additionalProperties": False
        }
    },
    "required": ["curation_decision", "editor_brief", "curation_output"],
    "additionalProperties": False
}

PREVIEW_SEPARATOR = "=" * 82


def _build_messages(config: CurateConfig, item: sqlite3.Row) -> Dict[str, str]:
    gov = item["governmental_involvement"]
    gov_str = str(gov) if gov is not None else "0"
    user_prompt = config.active_template.user_prompt_template.format(
        raw_title=item["raw_title"],
        sanitized_text=item["sanitized_text"],
        topic_class=item["topic_class"],
        governmental_involvement=gov_str
    )
    return {
        "system_instruction": config.active_template.system_instruction,
        "user_prompt": user_prompt,
    }


def _build_request_payload(config: CurateConfig, item: sqlite3.Row) -> Dict[str, Any]:
    provider = config.active_provider
    defaults = config.request_defaults
    messages = _build_messages(config, item)

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
                "name": "curation_result",
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
        raise ModelRefusalError(f"Model refused to curate: {message['refusal']}")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "LLM API returned content block that is not a non-empty string "
            f"(got type {type(content).__name__})"
        )

    return json.loads(content)


def _print_preview_items(config: CurateConfig, pending_items: list[Any]) -> None:
    print(PREVIEW_SEPARATOR)
    print(f"PREVIEW PROMPTS MODE: Listing {len(pending_items)} pending prompt payloads")
    print(PREVIEW_SEPARATOR)
    for idx, item in enumerate(pending_items, 1):
        print(f"[{idx}] Source Item ID: {item['source_item_id']}")
        print(f"    Raw Title: {item['raw_title']}")
        print(f"    Topic Class: {item['topic_class']}")
        messages = _build_messages(config, item)
        print("    ---------------------------------------------------------")
        print("    System Message:")
        print(messages["system_instruction"].strip())
        print("    User Message:")
        print(messages["user_prompt"].strip())
        print(PREVIEW_SEPARATOR)


def validate_curation_response(data: Dict[str, Any]) -> None:
    """
    Validates parsed JSON response against curation policy and validation matrix.
    Raises ValueError on contract violation.
    """
    if not isinstance(data, dict):
        raise ValueError("Response data must be a dictionary")
    
    if "curation_decision" not in data:
        raise ValueError("Missing 'curation_decision' in response")
    
    decision = data["curation_decision"]
    if not isinstance(decision, dict):
        raise ValueError("'curation_decision' must be a dictionary")
        
    for k in ("curate_status", "downstream_action", "decision_reason"):
        if k not in decision:
            raise ValueError(f"Missing '{k}' in curation_decision")
            
    status = decision["curate_status"]
    action = decision["downstream_action"]
    reason = decision["decision_reason"]
    
    if status not in ("approved", "rejected"):
        raise ValueError(f"Invalid curate_status: {status}")
        
    if action not in ("publish_link", "publish_summary", "edit_rewrite", "reject_discard"):
        raise ValueError(f"Invalid downstream_action: {action}")
        
    if not isinstance(reason, str) or len(reason) > 250:
        raise ValueError("decision_reason must be a string up to 250 characters")
        
    # Check status-action alignment
    if status == "approved" and action not in ("publish_link", "publish_summary"):
        raise ValueError(f"Approved status is incompatible with action: {action}")
    if status == "rejected" and action not in ("edit_rewrite", "reject_discard"):
        raise ValueError(f"Rejected status is incompatible with action: {action}")
        
    brief = data.get("editor_brief")
    output = data.get("curation_output")
    
    # Conditional checks based on action
    if action == "reject_discard":
        if brief is not None:
            raise ValueError("editor_brief must be null for 'reject_discard'")
        if output is not None:
            raise ValueError("curation_output must be null for 'reject_discard'")
            
    elif action == "edit_rewrite":
        if brief is None or not isinstance(brief, dict):
            raise ValueError("editor_brief must be a non-null object for 'edit_rewrite'")
        if output is not None:
            raise ValueError("curation_output must be null for 'edit_rewrite'")
            
        # Validate brief fields
        for k in ("brief_goal", "target_format", "risk_flags", "tone_guidance"):
            if k not in brief:
                raise ValueError(f"Missing '{k}' in editor_brief for 'edit_rewrite'")
        if brief["target_format"] not in ("link_card", "structured_summary"):
            raise ValueError(f"Invalid target_format in brief: {brief['target_format']}")
        if not isinstance(brief["risk_flags"], list):
            raise ValueError("risk_flags must be an array")
            
    elif action == "publish_link":
        if brief is None or not isinstance(brief, dict):
            raise ValueError("editor_brief must be a non-null object for 'publish_link'")
        if output is None or not isinstance(output, dict):
            raise ValueError("curation_output must be a non-null object for 'publish_link'")
            
        # Validate brief
        for k in ("brief_goal", "target_format", "risk_flags", "tone_guidance"):
            if k not in brief:
                raise ValueError(f"Missing '{k}' in editor_brief for 'publish_link'")
        if brief["target_format"] != "link_card":
            raise ValueError("target_format in editor_brief must be 'link_card' for 'publish_link'")
        if not isinstance(brief["risk_flags"], list):
            raise ValueError("risk_flags must be an array")
            
        # Validate output
        for k in ("display_title", "summary_short"):
            if k not in output:
                raise ValueError(f"Missing '{k}' in curation_output for 'publish_link'")
        if not isinstance(output["display_title"], str) or len(output["display_title"]) > 250:
            raise ValueError("display_title must be a string up to 250 characters")
        if not isinstance(output["summary_short"], str) or len(output["summary_short"]) > 500:
            raise ValueError("summary_short must be a string up to 500 characters")
            
        # Bullets must be null
        for bullet in ("bullet_1", "bullet_2", "bullet_3"):
            if output.get(bullet) is not None:
                raise ValueError(f"bullet points must be null for 'publish_link'")
                
    elif action == "publish_summary":
        if brief is None or not isinstance(brief, dict):
            raise ValueError("editor_brief must be a non-null object for 'publish_summary'")
        if output is None or not isinstance(output, dict):
            raise ValueError("curation_output must be a non-null object for 'publish_summary'")
            
        # Validate brief
        for k in ("brief_goal", "target_format", "risk_flags", "tone_guidance"):
            if k not in brief:
                raise ValueError(f"Missing '{k}' in editor_brief for 'publish_summary'")
        if brief["target_format"] != "structured_summary":
            raise ValueError("target_format in editor_brief must be 'structured_summary' for 'publish_summary'")
        if not isinstance(brief["risk_flags"], list):
            raise ValueError("risk_flags must be an array")
            
        # Validate output
        for k in ("display_title", "summary_short", "bullet_1", "bullet_2", "bullet_3"):
            if k not in output:
                raise ValueError(f"Missing '{k}' in curation_output for 'publish_summary'")
        if not isinstance(output["display_title"], str) or len(output["display_title"]) > 250:
            raise ValueError("display_title must be a string up to 250 characters")
        if not isinstance(output["summary_short"], str) or len(output["summary_short"]) > 500:
            raise ValueError("summary_short must be a string up to 500 characters")
            
        # Bullets must all be NOT NULL and strings
        for bullet in ("bullet_1", "bullet_2", "bullet_3"):
            val = output.get(bullet)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"bullet points must be non-empty strings for 'publish_summary'")


async def fetch_llm_curation(
    client: httpx.AsyncClient,
    config: CurateConfig,
    item: sqlite3.Row,
    api_key: str
) -> Dict[str, Any]:
    """
    Submits prompt to active LLM provider with exponential backoff retries.
    Returns validated raw JSON object.
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

    payload = _build_request_payload(config, item=item)

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

            # Validate against schema and matrix rules
            validate_curation_response(parsed_json)
            return parsed_json

        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            # Check if we have remaining attempts
            if attempt == attempts:
                raise RuntimeError(f"Curation request failed after {attempts} attempts. Last error: {exc}")

            # Exponential backoff with jitter
            sleep_duration = (backoff_factor ** attempt) + random.uniform(0.1, 1.0)
            print(f"  Attempt {attempt} failed ({exc}). Retrying in {sleep_duration:.2f} seconds...", file=sys.stderr)
            await asyncio.sleep(sleep_duration)

    raise RuntimeError("Unreachable")


async def curate_item(
    repo: CurationRepository,
    client: httpx.AsyncClient,
    config: CurateConfig,
    item: sqlite3.Row,
    api_key: str,
    db_lock: asyncio.Lock,
    commit: bool = True
) -> bool:
    """
    Processes a single item: checks pre-existing curation state, runs LLM curation,
    performs conditional updates and data cleanups, and records errors.
    Returns True if successfully processed, False if failure (and logs to stderr for forced re-runs).
    """
    item_id = item["source_item_id"]
    
    # 1. Inspect existing state to determine if this is a completed item run (forced re-run)
    existing = repo.get_curation_decision(item_id)
    was_completed = False
    existing_retry_count = 0
    if existing:
        existing_retry_count = existing["retry_count"]
        if existing["curate_status"] in ("approved", "rejected"):
            was_completed = True

    try:
        # 2. Call LLM for curation decision
        parsed = await fetch_llm_curation(
            client=client,
            config=config,
            item=item,
            api_key=api_key
        )
        
        # Extract parsed fields
        decision_raw = parsed["curation_decision"]
        status = decision_raw["curate_status"]
        action = decision_raw["downstream_action"]
        reason = decision_raw["decision_reason"]
        
        brief_raw = parsed.get("editor_brief")
        output_raw = parsed.get("curation_output")

        # 3. Transaction boundary
        async with db_lock:
            with transaction(repo.conn, commit=commit):
                # Write/Update curation_decision (retry_count reset to 0)
                repo.upsert_curation_decision({
                    "source_item_id": item_id,
                    "curate_status": status,
                    "downstream_action": action,
                    "decision_reason": reason,
                    "retry_count": 0,
                    "model_name": config.active_provider.model_name,
                    "prompt_version": config.active_template.version
                })

                # Conditional write or deletion of brief & output based on downstream action
                if action == "publish_link" or action == "publish_summary":
                    # editor_brief MUST exist
                    repo.upsert_editor_brief({
                        "source_item_id": item_id,
                        **brief_raw
                    })
                    # curation_output MUST exist
                    repo.upsert_curation_output({
                        "source_item_id": item_id,
                        **output_raw
                    })

                elif action == "edit_rewrite":
                    # editor_brief MUST exist
                    repo.upsert_editor_brief({
                        "source_item_id": item_id,
                        **brief_raw
                    })
                    # curation_output MUST NOT exist
                    repo.delete_curation_output(item_id)

                elif action == "reject_discard":
                    # editor_brief and curation_output MUST NOT exist
                    repo.delete_editor_brief(item_id)
                    repo.delete_curation_output(item_id)

        return True

    except Exception as exc:
        # Error handling policies
        if was_completed:
            # Operator-Forced Re-run Failure:
            # DO NOT overwrite DB, DO NOT increment retry counter, rollback transaction.
            print(f"Forced re-run failed for completed item ID {item_id}: {exc}", file=sys.stderr)
            return False
        else:
            # Normal Queue/Failed Run Curation Failure:
            # Trapped exception: update decision to failed, set action=NULL, increment retry count
            print(f"Error curating item ID {item_id}: {exc}", file=sys.stderr)
            try:
                async with db_lock:
                    with transaction(repo.conn, commit=commit):
                        repo.upsert_curation_decision({
                            "source_item_id": item_id,
                            "curate_status": "failed",
                            "downstream_action": None,
                            "decision_reason": str(exc)[:245],
                            "retry_count": existing_retry_count + 1,
                            "model_name": config.active_provider.model_name,
                            "prompt_version": config.active_template.version
                        })
            except Exception as inner_exc:
                print(f"Failed to record curation failure for item ID {item_id}: {inner_exc}", file=sys.stderr)
            return False


async def orchestrate_run(
    config: CurateConfig,
    db_path: pathlib.Path,
    batch_size: Optional[int] = None,
    preview_prompts: bool = False,
    dry_run: bool = False,
    source_item_id: Optional[int] = None,
    force: bool = False
) -> Dict[str, Any]:
    """
    Orchestrates the curation batch run.
    Uses file locking to avoid multi-process concurrency issues on SQLite,
    schedules items with rate limits, and updates database records cleanly.
    """
    # 1. Multi-Process Lock Coordination
    workspace_root = db_path.parent.parent
    lock_file = workspace_root / "data" / "curate_runner.lock"
    process_lock = ProcessLock(lock_file)
    
    # Do not acquire file lock in dry-run/preview mode
    if not preview_prompts and not dry_run:
        try:
            process_lock.acquire()
        except RuntimeError as err:
            print(str(err), file=sys.stderr)
            raise

    # 2. Database connection & Repository setup
    conn = get_connection(db_path)
    repo = CurationRepository(conn)

    # Respect batch overrides
    run_batch_size = batch_size if batch_size is not None else config.execution_policy.batch_size

    try:
        # Load pending items
        if source_item_id is not None:
            item = repo.get_item_by_id(source_item_id)
            if not item:
                raise ValueError(f"Source item with ID {source_item_id} not found, or is not classified as core/adjacent.")
            
            existing = repo.get_curation_decision(source_item_id)
            if existing and not force:
                if existing["curate_status"] in ("approved", "rejected"):
                    raise ValueError(f"Source item with ID {source_item_id} has already been curated (status: {existing['curate_status']}, action: {existing['downstream_action']}). Use --force to re-curate.")
                elif existing["curate_status"] == "failed" and existing["retry_count"] >= 3:
                    raise ValueError(f"Source item with ID {source_item_id} is locked (failed {existing['retry_count']} times). Use --force to override.")
            
            pending_items = [item]
        else:
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

        # API keys loading
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
                    # Execute curation
                    # Under dry_run, we process fully but do not commit the transaction
                    success = await curate_item(
                        repo=repo,
                        client=client,
                        config=config,
                        item=item,
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
        if not preview_prompts and not dry_run:
            process_lock.release()
