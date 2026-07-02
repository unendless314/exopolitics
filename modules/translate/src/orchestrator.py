import asyncio
import json
import os
import pathlib
import random
import sqlite3
import sys
import re
from typing import Dict, Any, Optional, Tuple, List

import httpx

from .config import TranslateConfig
from .database import (
    get_connection,
    transaction,
    TranslationRepository,
    get_utc_now_iso8601
)

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


def validate_translation_response(
    data: Dict[str, Any],
    target_language_code: str,
    source_content_body: str,
    max_title_len: int,
    content_ratio_limit: float
) -> None:
    """
    Executes three levels of content validation:
    1. Character length ratio limit check.
    2. Target title length cap check.
    3. Markdown structural integrity check (code fences, link parens/brackets, headers).
    Raises ValueError on validation failure.
    """
    if not isinstance(data, dict):
        raise ValueError("Response data must be a dictionary")
    if "translated_title" not in data or "translated_content" not in data:
        raise ValueError("Response must contain 'translated_title' and 'translated_content'")
    
    title = data["translated_title"]
    content = data["translated_content"]
    
    if not isinstance(title, str) or not isinstance(content, str):
        raise ValueError("translated_title and translated_content must be strings")
        
    # 1. Character Length Ratio Check
    if len(source_content_body) > 0:
        ratio = len(content) / len(source_content_body)
        if ratio > content_ratio_limit:
            raise ValueError(f"Translated content length ratio ({ratio:.2f}) exceeds limit of {content_ratio_limit}")
            
    # 2. Title Length Check
    if len(title) > max_title_len:
        raise ValueError(f"Translated title length ({len(title)}) exceeds limit of {max_title_len}")
    
    # 2.1 Japanese specific title check
    if target_language_code == 'ja' and len(title) > 120:
        raise ValueError(f"Japanese title length ({len(title)}) exceeds double-byte cap of 120 characters")
        
    # 3. Markdown Structural Check
    # Code fence symmetry
    fence_count_source = source_content_body.count("```")
    fence_count_translated = content.count("```")
    if fence_count_translated % 2 != 0:
        raise ValueError("Translated content has odd number of code fences")
    if fence_count_translated != fence_count_source:
        raise ValueError(f"Code fence count mismatch. Source: {fence_count_source}, Translated: {fence_count_translated}")
        
    # Link syntax preservation
    # 1. Global bracket balance check (brackets are almost exclusively used for links/markdown structures)
    if content.count("[") != content.count("]"):
        raise ValueError("Mismatched square brackets in markdown links")

    # 2. Detailed parenthetical balance check for link targets (using adjacent '](')
    idx = 0
    while True:
        idx = content.find("](", idx)
        if idx == -1:
            break
            
        # Scan backward from idx - 1 to find '['
        back_idx = idx - 1
        found_open = False
        while back_idx >= 0:
            if content[back_idx] == '[':
                found_open = True
                break
            elif content[back_idx] == ']':
                # Hit another closing bracket before finding opening one
                break
            back_idx -= 1
        if not found_open:
            raise ValueError("Malformed markdown link: closing bracket without opening bracket or nested link structure")
            
        # Scan forward from idx + 2 to find matching ')'
        forward_idx = idx + 2
        paren_depth = 1  # We have seen '(' at the start of link target: ](
        found_close = False
        while forward_idx < len(content):
            char = content[forward_idx]
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    found_close = True
                    break
            elif char in ('\n', '\r'):
                # Markdown link URLs cannot span multiple lines
                break
            forward_idx += 1
        if not found_close:
            raise ValueError("Malformed markdown link: URL parenthesis not closed properly")
            
        idx += 2
        
    # Header preservation
    def get_header_structure(text: str) -> List[int]:
        levels = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                match = re.match(r"^(#+)\s+", stripped)
                if match:
                    levels.append(len(match.group(1)))
        return levels

    source_headers = get_header_structure(source_content_body)
    translated_headers = get_header_structure(content)
    if source_headers != translated_headers:
        raise ValueError(f"Header structure mismatch. Source: {source_headers}, Translated: {translated_headers}")

    # 4. Target Script Presence Validation (Proper-noun-tolerant)
    if target_language_code == 'zh':
        # CJK Unified Ideographs (Chinese characters)
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in content)
        if not has_chinese:
            raise ValueError("Chinese translation output lacks CJK Unified Ideographs (Chinese characters)")
    elif target_language_code == 'ja':
        # Hiragana and Katakana characters (essential grammatical elements of written Japanese)
        has_kana = any(
            ('\u3040' <= char <= '\u309f') or ('\u30a0' <= char <= '\u30ff')
            for char in content
        )
        if not has_kana:
            raise ValueError("Japanese translation output lacks Hiragana/Katakana characters")


def _build_request_payload(config: TranslateConfig, item: sqlite3.Row, target_language_code: str) -> Dict[str, Any]:
    provider = config.active_provider
    defaults = config.request_defaults
    
    # Get target language label
    lang_config = config.target_languages.get(target_language_code)
    target_language_label = lang_config.label if lang_config else target_language_code
    target_language_str = f"{target_language_label} ({target_language_code})"

    system_instruction = config.active_template.system_instruction
    user_prompt = config.active_template.user_prompt_template.format(
        target_language=target_language_str,
        display_title=item["display_title"],
        content_body=item["content_body"]
    )

    payload: Dict[str, Any] = {
        "model": provider.model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": defaults.temperature,
        "top_p": defaults.top_p,
        "max_tokens": defaults.max_output_tokens,
    }

    if provider.supports_structured_output:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_result",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "translated_title": {
                            "type": "string",
                            "maxLength": 500
                        },
                        "translated_content": {
                            "type": "string"
                        }
                    },
                    "required": ["translated_title", "translated_content"],
                    "additionalProperties": False
                },
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
        raise ValueError(f"Model refused to translate: {message['refusal']}")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "LLM API returned content block that is not a non-empty string "
            f"(got type {type(content).__name__})"
        )

    return json.loads(content)


def _print_preview_item(config: TranslateConfig, item: sqlite3.Row, target_lang: str) -> None:
    separator = "=" * 82
    print(separator)
    print(f"PREVIEW TRANSLATION PROMPT: parent_content_id={item['parent_content_id']}, target_lang={target_lang}")
    print(separator)
    payload = _build_request_payload(config, item, target_lang)
    print("System Message:")
    print(payload["messages"][0]["content"].strip())
    print("User Message:")
    print(payload["messages"][1]["content"].strip())
    print(separator)


async def fetch_llm_translation(
    client: httpx.AsyncClient,
    config: TranslateConfig,
    item: sqlite3.Row,
    target_language_code: str,
    api_key: str
) -> Dict[str, Any]:
    """
    Submits translation request to the active LLM provider with backoff retries.
    Returns validated JSON response with translated title and content.
    """
    provider = config.active_provider
    policy = config.execution_policy
    validation = config.validation

    # Resolve limits for target language
    lang_config = config.target_languages.get(target_language_code)
    max_title_len = lang_config.max_title_length if lang_config else validation.default_max_title_length
    content_ratio_limit = validation.content_ratio_limit

    # Setup API endpoint
    api_base = provider.api_base or "https://api.openai.com/v1"
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = _build_request_payload(config, item, target_language_code)

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

            # Check for rate limiting (429) or server errors (5xx)
            if response.status_code == 429 or (500 <= response.status_code < 600):
                response.raise_for_status()

            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"LLM API returned client error status {response.status_code}",
                    request=response.request,
                    response=response
                )

            parsed_json = _parse_response_content(response)

            # Validate the output structure and values
            validate_translation_response(
                parsed_json,
                target_language_code=target_language_code,
                source_content_body=item["content_body"],
                max_title_len=max_title_len,
                content_ratio_limit=content_ratio_limit
            )
            return parsed_json

        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            if attempt == attempts:
                raise RuntimeError(f"Translation request failed after {attempts} attempts. Last error: {exc}")

            sleep_duration = (backoff_factor ** attempt) + random.uniform(0.1, 1.0)
            print(f"  Attempt {attempt} failed ({exc}). Retrying in {sleep_duration:.2f} seconds...", file=sys.stderr)
            await asyncio.sleep(sleep_duration)

    raise RuntimeError("Unreachable")


async def translate_task(
    repo: TranslationRepository,
    client: httpx.AsyncClient,
    config: TranslateConfig,
    task: Dict[str, Any],
    api_key: str,
    db_lock: asyncio.Lock,
    commit: bool = True
) -> bool:
    """
    Processes a single translation task (one language for one mother-draft).
    Implements self-translation bypass and strict failure/rollback rules.
    """
    parent_content_id = task["parent_content_id"]
    source_item_id = task["source_item_id"]
    target_language = task["language_code"]
    source_language = task["content_language_code"]
    
    # 1. Determine if this is an operator-forced re-run of a completed item
    existing = repo.get_translation_output(parent_content_id, target_language)
    # The task has task["status"] == "completed" only if it was selected through the force path in orchestrate_run
    is_forced = (task.get("status") == "completed")
    existing_retry_count = existing["retry_count"] if existing else 0

    try:
        # 2. Check for self-translation bypass
        if target_language == source_language:
            # Self-Translation Bypass Policy
            # Direct copy display_title and content_body
            now = get_utc_now_iso8601()
            async with db_lock:
                try:
                    repo.conn.rollback()
                except Exception:
                    pass
                with transaction(repo.conn, commit=commit):
                    repo.upsert_translation_output({
                        "parent_content_id": parent_content_id,
                        "source_item_id": source_item_id,
                        "language_code": target_language,
                        "display_title": task["display_title"],
                        "content": task["content_body"],
                        "source_fingerprint": task["content_fingerprint"],
                        "translation_status": "completed",
                        "retry_count": 0,
                        "model_name": "bypass",
                        "prompt_version": "bypass",
                        "translated_at": now
                    })
            return True

        # Fetch candidate content record
        item = repo.get_approved_content_by_id(parent_content_id)
        if not item:
            raise ValueError(f"Approved content record ID {parent_content_id} not found in database.")

        # 3. Request LLM Translation
        parsed = await fetch_llm_translation(
            client=client,
            config=config,
            item=item,
            target_language_code=target_language,
            api_key=api_key
        )

        now = get_utc_now_iso8601()

        # 4. Database Transaction write
        async with db_lock:
            try:
                repo.conn.rollback()
            except Exception:
                pass
            with transaction(repo.conn, commit=commit):
                repo.upsert_translation_output({
                    "parent_content_id": parent_content_id,
                    "source_item_id": source_item_id,
                    "language_code": target_language,
                    "display_title": parsed["translated_title"],
                    "content": parsed["translated_content"],
                    "source_fingerprint": task["content_fingerprint"],
                    "translation_status": "completed",
                    "retry_count": 0,
                    "model_name": config.active_provider.model_name,
                    "prompt_version": config.active_template.version,
                    "translated_at": now
                })
        return True

    except Exception as exc:
        try:
            repo.conn.rollback()
        except Exception:
            pass

        if is_forced:
            # Operator Forced Re-run failure:
            # Rollback transaction, keep existing translated text unchanged, log to stderr
            print(f"Forced re-run failed for completed task ({parent_content_id}, '{target_language}'): {exc}", file=sys.stderr)
            return False
        else:
            # First-time / non-completed task failure:
            # Write 'failed', increment retry_count, keep content NULL if first run
            print(f"Error translating task ({parent_content_id}, '{target_language}'): {exc}", file=sys.stderr)
            try:
                # Retrieve existing values to preserve them if this was not the first run
                old_title = existing["display_title"] if existing else None
                old_content = existing["content"] if existing else None
                
                async with db_lock:
                    try:
                        repo.conn.rollback()
                    except Exception:
                        pass
                    with transaction(repo.conn, commit=commit):
                        repo.upsert_translation_output({
                            "parent_content_id": parent_content_id,
                            "source_item_id": source_item_id,
                            "language_code": target_language,
                            "display_title": old_title,
                            "content": old_content,
                            "source_fingerprint": task["content_fingerprint"],
                            "translation_status": "failed",
                            "retry_count": existing_retry_count + 1,
                            "model_name": config.active_provider.model_name,
                            "prompt_version": config.active_template.version,
                            "translated_at": existing["translated_at"] if existing else None
                        })
            except Exception as inner_exc:
                print(f"Failed to record translation failure for task: {inner_exc}", file=sys.stderr)
            return False


async def orchestrate_run(
    config: TranslateConfig,
    db_path: pathlib.Path,
    batch_size: Optional[int] = None,
    preview_prompts: bool = False,
    dry_run: bool = False,
    parent_content_id: Optional[int] = None,
    language_code: Optional[str] = None,
    force: bool = False
) -> Dict[str, Any]:
    """
    Orchestrates the translation queue run.
    """
    # 1. Multi-process Runner Lock
    workspace_root = db_path.parent.parent
    lock_file = workspace_root / "data" / "translate_runner.lock"
    process_lock = ProcessLock(lock_file)

    if not preview_prompts and not dry_run:
        try:
            process_lock.acquire()
        except RuntimeError as err:
            print(str(err), file=sys.stderr)
            raise

    conn = get_connection(db_path)
    repo = TranslationRepository(conn)

    run_batch_size = batch_size if batch_size is not None else config.execution_policy.batch_size
    target_langs = list(config.target_languages.keys())

    try:
        # 2. Run stale cache invalidation detection first (before queuing tasks)
        if not preview_prompts and not dry_run:
            with transaction(conn, commit=True):
                staled_records = repo.detect_and_mark_stale(
                    running_model=config.active_provider.model_name,
                    running_prompt_version=config.active_template.version
                )
            if staled_records:
                print(f"Detected and marked {len(staled_records)} translation records as stale.")

        # 3. Load tasks
        all_tasks = []
        if parent_content_id is not None:
            # Single task mode
            item = repo.get_approved_content_by_id(parent_content_id)
            if not item:
                raise ValueError(f"Approved content record ID {parent_content_id} not found in database.")
                
            langs_to_process = [language_code] if language_code else target_langs
            for lang in langs_to_process:
                existing = repo.get_translation_output(parent_content_id, lang)
                
                # Check eligibility
                is_eligible = False
                retry_cnt = 0
                
                if not existing:
                    is_eligible = True
                elif force:
                    is_eligible = True
                else:
                    status = existing["translation_status"]
                    retry_cnt = existing["retry_count"]
                    if status in ("pending", "stale") or (status == "failed" and retry_cnt < config.execution_policy.retry_attempts):
                        is_eligible = True
                        
                if is_eligible:
                    all_tasks.append({
                        "parent_content_id": item["parent_content_id"],
                        "source_item_id": item["source_item_id"],
                        "display_title": item["display_title"],
                        "content_body": item["content_body"],
                        "content_fingerprint": item["content_fingerprint"],
                        "content_language_code": item["content_language_code"],
                        "approved_at": item["approved_at"],
                        "language_code": lang,
                        "status": existing["translation_status"] if existing else "new",
                        "retry_count": retry_cnt,
                    })
        else:
            # Bulk queue loader
            all_tasks = repo.get_pending_translation_tasks(
                target_languages=target_langs,
                retry_attempts=config.execution_policy.retry_attempts
            )

        # Clear any implicit read transactions from task loading before launching workers
        try:
            conn.rollback()
        except Exception:
            pass

        # Slice to batch size
        pending_tasks = all_tasks[:run_batch_size]

        if not pending_tasks:
            return {
                "total_queried": 0,
                "processed_successfully": 0,
                "failures": 0,
                "status": "completed"
            }

        # 4. Preview prompts mode
        if preview_prompts:
            for task in pending_tasks:
                # Load corresponding mother draft to get source details
                item = repo.get_approved_content_by_id(task["parent_content_id"])
                if item:
                    _print_preview_item(config, item, task["language_code"])
            return {
                "total_queried": len(pending_tasks),
                "processed_successfully": 0,
                "previewed": len(pending_tasks),
                "failures": 0,
                "status": "preview"
            }

        # Load API keys
        api_key_env = config.active_provider.api_key_env
        api_key = os.environ.get(api_key_env, "")
        
        # Self-translations do not require API keys, check if all tasks are self-translations
        all_are_bypasses = all(task["language_code"] == task["content_language_code"] for task in pending_tasks)
        if not api_key and not all_are_bypasses:
            raise ValueError(
                f"Missing required API key environment variable '{api_key_env}' for active provider '{config.active_provider_name}'"
            )

        # Concurrency and SQLite Write Lock setup
        semaphore = asyncio.Semaphore(config.execution_policy.max_concurrent_requests)
        db_lock = asyncio.Lock()

        # Calculate rate limit stagger delay
        rpm = config.execution_policy.rate_limit_per_minute
        request_delay = 60.0 / rpm if rpm > 0 else 0.0

        async with httpx.AsyncClient() as client:
            workers = []
            
            async def worker(task, idx):
                # Apply rate limit staggering if not a bypass task
                is_bypass = task["language_code"] == task["content_language_code"]
                if not is_bypass and idx > 0 and request_delay > 0:
                    await asyncio.sleep(idx * request_delay)

                async with semaphore:
                    success = await translate_task(
                        repo=repo,
                        client=client,
                        config=config,
                        task=task,
                        api_key=api_key,
                        db_lock=db_lock,
                        commit=not dry_run
                    )
                    return success

            for idx, task in enumerate(pending_tasks):
                workers.append(worker(task, idx))

            results = await asyncio.gather(*workers)

        succeeded = sum(1 for r in results if r)
        failed = len(results) - succeeded

        return {
            "total_queried": len(pending_tasks),
            "processed_successfully": succeeded,
            "failures": failed,
            "status": "completed"
        }

    finally:
        conn.close()
        if not preview_prompts and not dry_run:
            process_lock.release()
