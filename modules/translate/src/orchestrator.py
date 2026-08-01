import asyncio
import json
import os
import pathlib
import random
import sqlite3
import sys
import re
from typing import Dict, Any, Optional

import httpx

from .config import TranslateConfig
from .database import (
    get_connection,
    transaction,
    TranslationRepository,
    get_utc_now_iso8601
)

class NonRetryableLLMError(Exception):
    """Permanent LLM API failure that must not consume the retry budget.

    Raised for non-429 HTTP 4xx client errors (EXECUTION_POLICY.md section 4):
    an identical retry cannot fix a request/contract problem, so the task
    fails immediately instead of looping through the backoff policy.
    Only provider-documented retryable 4xx statuses may be added to the
    retry policy in the future.
    """

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


# Migration-period label guard list (EXECUTION_POLICY.md section 5 rule 5):
# the three English UI labels plus every zh/ja variant observed leaking into
# content (known_issues/resolved/TRANSLATION_LABEL_LEAKAGE.md section 4.2).
KNOWN_UI_LABELS = (
    "Key Claim",
    "Evidence Level",
    "Objective Impact",
    "主要主張",
    "關鍵主張",
    "核心主張",
    "證據層級",
    "證據等級",
    "客觀影響",
    "實際影響",
    "主要な主張",
    "主張の要点",
    "証拠の水準",
    "証拠レベル",
    "証拠水準",
    "エビデンスレベル",
    "客観的な影響",
    "客観的影響",
    "目的上の影響",
)

# Matches a value that, after leading whitespace and an optional list marker
# and/or Markdown emphasis opener, starts with a known UI label followed by
# an ASCII or fullwidth colon. Applied to zh/ja targets only.
_LABEL_GUARD_PATTERN = re.compile(
    r"^\s*(?:[*\-+]\s+)?(?:\*\*|__|\*|_)?\s*"
    r"(?:" + "|".join(re.escape(label) for label in sorted(KNOWN_UI_LABELS, key=len, reverse=True)) + r")"
    r"(?:\*\*|__|\*|_)?\s*[:：]"
)


def validate_translation_response(
    data: Dict[str, Any],
    target_language_code: str,
    source_summary: str,
    source_bullet_1: Optional[str],
    source_bullet_2: Optional[str],
    source_bullet_3: Optional[str],
    max_title_len: int,
    content_ratio_limit: float
) -> None:
    """
    Executes runner-side content validation on a translator_v2 five-field response:
    1. Required keys and non-empty-string type checks (nullability shape match).
    2. Title length caps (configured limit, plus the Japanese 120 double-byte cap).
    3. Aggregate content length ratio check over summary + non-null bullets.
    4. Target script presence check over the aggregated translated content.
    5. Migration-period label guard (zh/ja only).
    Raises ValueError on validation failure.
    """
    if not isinstance(data, dict):
        raise ValueError("Response data must be a dictionary")

    required_keys = (
        "translated_title",
        "translated_summary",
        "translated_bullet_1",
        "translated_bullet_2",
        "translated_bullet_3",
    )
    missing_keys = [key for key in required_keys if key not in data]
    if missing_keys:
        raise ValueError(f"Response is missing required keys: {', '.join(missing_keys)}")

    title = data["translated_title"]
    summary = data["translated_summary"]
    bullets = [data["translated_bullet_1"], data["translated_bullet_2"], data["translated_bullet_3"]]
    source_bullets = [source_bullet_1, source_bullet_2, source_bullet_3]

    # 1. Required string checks
    if not isinstance(title, str) or not title.strip():
        raise ValueError("translated_title must be a non-empty string after trimming")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("translated_summary must be a non-empty string after trimming")

    # 1b. Bullet type checks and source/response nullability shape match
    for idx, (source_bullet, translated_bullet) in enumerate(zip(source_bullets, bullets), start=1):
        if translated_bullet is not None and (not isinstance(translated_bullet, str) or not translated_bullet.strip()):
            raise ValueError(f"translated_bullet_{idx} must be null or a non-empty string after trimming")
        if source_bullet is None and translated_bullet is not None:
            raise ValueError(f"translated_bullet_{idx} must be null because source bullet_{idx} is null")
        if source_bullet is not None and translated_bullet is None:
            raise ValueError(f"translated_bullet_{idx} must be a non-empty string because source bullet_{idx} is non-empty")

    # 2. Title Length Check
    if len(title) > max_title_len:
        raise ValueError(f"Translated title length ({len(title)}) exceeds limit of {max_title_len}")

    # 2.1 Japanese specific title check
    if target_language_code == 'ja' and len(title) > 120:
        raise ValueError(f"Japanese title length ({len(title)}) exceeds double-byte cap of 120 characters")

    # 3. Aggregate Content Length Ratio Check
    # Computed over summary + non-null bullets on both sides, never over a
    # single short bullet, to avoid false rejections of valid translations.
    source_aggregate_len = len(source_summary) + sum(len(b) for b in source_bullets if b is not None)
    translated_aggregate_len = len(summary) + sum(len(b) for b in bullets if b is not None)
    if source_aggregate_len > 0:
        ratio = translated_aggregate_len / source_aggregate_len
        if ratio > content_ratio_limit:
            raise ValueError(f"Translated content length ratio ({ratio:.2f}) exceeds limit of {content_ratio_limit}")

    # 4. Target Script Presence Validation (Proper-noun-tolerant)
    # Applies to the aggregated translated content (summary + non-null bullets,
    # excluding the title).
    aggregated_translated = summary + "".join(b for b in bullets if b is not None)
    if target_language_code == 'zh':
        # CJK Unified Ideographs (Chinese characters)
        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in aggregated_translated)
        if not has_chinese:
            raise ValueError("Chinese translation output lacks CJK Unified Ideographs (Chinese characters)")
    elif target_language_code == 'ja':
        # Hiragana and Katakana characters (essential grammatical elements of written Japanese)
        has_kana = any(
            ('\u3040' <= char <= '\u309f') or ('\u30a0' <= char <= '\u30ff')
            for char in aggregated_translated
        )
        if not has_kana:
            raise ValueError("Japanese translation output lacks Hiragana/Katakana characters")

    # 5. Migration-Period Label Guard (zh/ja targets only)
    # Detects erroneous presentation-string backflow into content. This is not
    # the primary correctness mechanism; global string replacement on content
    # is forbidden.
    if target_language_code in ('zh', 'ja'):
        for value in [summary] + [b for b in bullets if b is not None]:
            if _LABEL_GUARD_PATTERN.search(value):
                raise ValueError(
                    "Translated field starts with a known UI presentation label prefix; "
                    "presentation labels must not appear in content values"
                )


def _build_request_payload(config: TranslateConfig, item: sqlite3.Row, target_language_code: str) -> Dict[str, Any]:
    provider = config.active_provider
    defaults = config.request_defaults
    
    # Get target language label
    lang_config = config.target_languages.get(target_language_code)
    target_language_label = lang_config.label if lang_config else target_language_code
    target_language_str = f"{target_language_label} ({target_language_code})"

    # NULL source bullets are rendered as the JSON literal `null` so the model
    # knows the corresponding response value must be null.
    def render_bullet(value: Optional[str]) -> str:
        return value if value is not None else "null"

    system_instruction = config.active_template.system_instruction
    user_prompt = config.active_template.user_prompt_template.format(
        target_language=target_language_str,
        display_title=item["display_title"],
        summary_short=item["summary_short"],
        bullet_1=render_bullet(item["bullet_1"]),
        bullet_2=render_bullet(item["bullet_2"]),
        bullet_3=render_bullet(item["bullet_3"])
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
                        "translated_summary": {
                            "type": "string"
                        },
                        "translated_bullet_1": {
                            "type": ["string", "null"]
                        },
                        "translated_bullet_2": {
                            "type": ["string", "null"]
                        },
                        "translated_bullet_3": {
                            "type": ["string", "null"]
                        }
                    },
                    "required": ["translated_title", "translated_summary", "translated_bullet_1", "translated_bullet_2", "translated_bullet_3"],
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
    Returns validated JSON response with the five translated fields.
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

            # Retryable statuses: rate limiting (429) or server errors (5xx).
            if response.status_code == 429 or (500 <= response.status_code < 600):
                response.raise_for_status()

            if response.status_code != 200:
                # Non-429 4xx (and any other unexpected status) is a permanent
                # client error: fail immediately without consuming the retry
                # budget (EXECUTION_POLICY.md section 4).
                raise NonRetryableLLMError(
                    f"LLM API returned non-retryable client error status {response.status_code} "
                    "(only 429 and 5xx are eligible for retry)"
                )

            parsed_json = _parse_response_content(response)

            # Validate the output structure and values
            validate_translation_response(
                parsed_json,
                target_language_code=target_language_code,
                source_summary=item["summary_short"],
                source_bullet_1=item["bullet_1"],
                source_bullet_2=item["bullet_2"],
                source_bullet_3=item["bullet_3"],
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
            # Direct copy of the five content fields
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
                        "summary_short": task["summary_short"],
                        "bullet_1": task["bullet_1"],
                        "bullet_2": task["bullet_2"],
                        "bullet_3": task["bullet_3"],
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
                    "summary_short": parsed["translated_summary"],
                    "bullet_1": parsed["translated_bullet_1"],
                    "bullet_2": parsed["translated_bullet_2"],
                    "bullet_3": parsed["translated_bullet_3"],
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
            # Write 'failed', increment retry_count, keep content NULL if first run.
            # A permanent (non-retryable) client error cannot be fixed by an
            # identical re-request, so the row is locked out of the automatic
            # queue immediately: retry_count is written at the configured retry
            # limit instead of incremented by one (EXECUTION_POLICY.md
            # section 4). An operator can still rerun the item via --force.
            if isinstance(exc, NonRetryableLLMError):
                new_retry_count = config.execution_policy.retry_attempts
                print(
                    f"Permanent failure translating task ({parent_content_id}, "
                    f"'{target_language}'): {exc} "
                    "(locked at retry limit; requires operator intervention)",
                    file=sys.stderr,
                )
            else:
                new_retry_count = existing_retry_count + 1
                print(f"Error translating task ({parent_content_id}, '{target_language}'): {exc}", file=sys.stderr)
            try:
                # Retrieve existing values to preserve them if this was not the first run
                old_title = existing["display_title"] if existing else None
                old_summary = existing["summary_short"] if existing else None
                old_bullet_1 = existing["bullet_1"] if existing else None
                old_bullet_2 = existing["bullet_2"] if existing else None
                old_bullet_3 = existing["bullet_3"] if existing else None
                
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
                            "summary_short": old_summary,
                            "bullet_1": old_bullet_1,
                            "bullet_2": old_bullet_2,
                            "bullet_3": old_bullet_3,
                            "source_fingerprint": task["content_fingerprint"],
                            "translation_status": "failed",
                            "retry_count": new_retry_count,
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
    # Validate the effective batch size first, before acquiring the process
    # lock or opening the database, so a rejected override leaves nothing
    # behind. batch_size counts source items and must be a positive integer
    # (EXECUTION_POLICY.md section 2); the CLI enforces the same rule at
    # option parsing and config validation enforces it for configured values.
    run_batch_size = batch_size if batch_size is not None else config.execution_policy.batch_size
    if (
        not isinstance(run_batch_size, int)
        or isinstance(run_batch_size, bool)
        or run_batch_size < 1
    ):
        raise ValueError(
            f"batch_size must be a positive integer (source-item unit), got {run_batch_size!r}"
        )

    # 1. Multi-process Runner Lock
    # --dry-run issues real LLM API requests, so it must hold the same
    # exclusive process lock as a normal run to prevent duplicate API
    # execution (EXECUTION_POLICY.md section 3). Only --preview-prompts skips
    # the lock because it never calls the API and never writes to the DB.
    workspace_root = db_path.parent.parent
    lock_file = workspace_root / "data" / "translate_runner.lock"
    process_lock = ProcessLock(lock_file)

    if not preview_prompts:
        try:
            process_lock.acquire()
        except RuntimeError as err:
            print(str(err), file=sys.stderr)
            raise

    conn = get_connection(db_path)
    repo = TranslationRepository(conn)

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
                        "summary_short": item["summary_short"],
                        "bullet_1": item["bullet_1"],
                        "bullet_2": item["bullet_2"],
                        "bullet_3": item["bullet_3"],
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

        # Slice to batch size BY SOURCE ITEM (EXECUTION_POLICY.md section 2):
        # batch_size counts distinct mother-drafts, not language tasks.
        # Select up to batch_size articles that have at least one eligible
        # translation, ordered by approved_at ASC with parent_content_id ASC
        # as the deterministic tie-breaker, then expand every eligible
        # language task of each selected article so a batch boundary never
        # splits one article's pending language set. API concurrency and
        # request frequency are governed separately by
        # max_concurrent_requests and rate_limit_per_minute.
        selected_parent_ids = []
        seen_parent_ids = set()
        ordered_tasks = sorted(
            all_tasks,
            key=lambda t: (t["approved_at"], t["parent_content_id"], t["language_code"])
        )
        for task in ordered_tasks:
            pid = task["parent_content_id"]
            if pid in seen_parent_ids:
                continue
            seen_parent_ids.add(pid)
            selected_parent_ids.append(pid)
            if len(selected_parent_ids) >= run_batch_size:
                break
        selected_id_set = set(selected_parent_ids)
        pending_tasks = [t for t in ordered_tasks if t["parent_content_id"] in selected_id_set]

        if not pending_tasks:
            return {
                "source_items": 0,
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
                "source_items": len(selected_parent_ids),
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
            "source_items": len(selected_parent_ids),
            "total_queried": len(pending_tasks),
            "processed_successfully": succeeded,
            "failures": failed,
            "status": "completed"
        }

    finally:
        conn.close()
        if not preview_prompts:
            process_lock.release()
