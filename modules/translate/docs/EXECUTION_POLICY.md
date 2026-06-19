# Translate Execution Policy

**Document version:** v1.1  
**Updated:** 2026-06-19  
**Status:** Locked Contract  

---

## 1. Purpose

This document defines execution controls, batching constraints, concurrent API limits, rate-limit handling, database transaction boundaries, and multi-process coordination for the `translate` module runner.

---

## 2. Queue Selection & Batching

* **Task Scope**: The unit of translation is defined by a pair of `(parent_content_id, language_code)`. For each approved mother-draft, separate translation tasks are executed for each configured target language.
* **Selection Criteria**: The runner fetches items from `approved_content_record` that meet any of the following conditions for a given target language:
  1. No matching row exists in the `translation_output` table for `(parent_content_id, language_code)`.
  2. A row exists in `translation_output` with `translation_status = 'pending'`.
  3. A row exists in `translation_output` with `translation_status = 'stale'`.
  4. A row exists in `translation_output` with `translation_status = 'failed'` and `retry_count < max_retries` (where `max_retries` is defined in `config/config.yaml`).
* **Batch Size**: Defaults to the value configured in `config/config.yaml` (e.g. `20` items, representing up to `20 * number of target languages` translation calls per execution run), which can be overridden via the `--batch-size` CLI flag.
* **Dry Run / Preview**: If `--preview-prompts` is supplied:
  * The runner prepares the inputs, constructs the prompts for each target language, and prints the generated payloads to stdout.
  * It must **not** invoke the LLM API and must **not** write any entries to the database.

---

## 3. Database Transactions & Concurrency

* **Multi-Process Runner Lock**:
  * To prevent duplicate API execution and lock contention in SQLite, the runner must acquire an exclusive file lock on `data/translate_runner.lock` at start.
  * If the lock cannot be acquired, the runner must log an error and exit immediately.
* **Concurrency Semaphore**:
  * Parallel execution of translation requests is managed asynchronously. Concurrency is limited by `max_concurrent_requests` (via `asyncio.Semaphore`, defaulting to the value configured in `config/config.yaml`, e.g. 5) to respect API rate limits.
* **Isolation of Network Calls**:
  * All LLM API translation calls must be executed **outside** database transactions. Holding database connections or write transactions open during network calls is strictly forbidden.
* **Granular Database Transactions**:
  * The transaction must only wrap the final write operations for a single translated item.
  * For each successfully translated language target:
    1. Acquire an `asyncio.Lock` to serialize database write access in the event loop.
    2. Open a short write transaction block using `BEGIN IMMEDIATE`.
    3. Upsert the row in `translation_output` with `translation_status = 'completed'`, updating fields like `translated_at`, `display_title`, `content`, `source_fingerprint`, and resetting `retry_count` to 0.
    4. Commit and close the transaction immediately.

---

## 4. Error Handling & Retry Policies

* **Transient Error Trapping**:
  * Network timeouts, rate limits (`429`), model overloaded (`503`), JSON parsing/schema validation, or runner-side validation failures must not crash the overall translation runner.
  * When an error occurs during a translation task:
    - The runner must catch the error.
    - Write a `'failed'` status in `translation_output` for `(parent_content_id, language_code)`. Keep `display_title` and `content` as `NULL` if this is the first execution (do not write empty strings or fake content).
    - Increment `retry_count` by 1.
    - If `retry_count >= max_retries` (where `max_retries` is configured in `config/config.yaml`, e.g. 3), the translation task is logically locked (excluded from automatic retries) and requires operator intervention.
* **Exponential Backoff**:
  * Implement exponential backoff (utilizing the backoff factor configured in `config/config.yaml`, e.g. 2.0 -> 2s, 4s, 8s) between retries during API execution to respect provider rate limits.
* **Operator Forced Re-run Error Handling**:
  * If a manual/operator-forced re-run is triggered for an already `completed` translation, any execution or validation failure must **not** overwrite the existing successful translation or increment the retry counter. The runner must rollback the database transaction, leaving the existing translated title and markdown content unchanged in the database, and log the failure to stderr.

---

## 5. Runner-Side Content Validation Rules

To guarantee markdown syntax preservation and translation quality, the runner must execute three levels of content validation after receiving the LLM structured JSON response:

1. **Character Length Ratio Check**:
   - Calculate the ratio: `len(translated_content) / len(content_body)`.
   - If the ratio is strictly greater than `1.2`, the validation fails. (This prevents LLM hallucination or excessive rambling).
2. **Japanese Title Length Check**:
   - If the target `language_code` is `'ja'`, verify that `len(translated_title) <= 120`.
   - If the length exceeds 120 double-byte characters, the validation fails.
3. **Markdown Structural Check**:
   - Validate code fence symmetry (even number of ```) and ensure markdown links `[text](url)` are intact.
   - Verify that all major heading structures (`#`, `##`, etc.) present in the source `content_body` are preserved in the translated output.
4. **Validation Mismatch Treatment**:
   - Any validation failure is treated as a transient runner error.
   - It triggers the same state updates as an API error (status = `'failed'`, `retry_count` increments, rollback on forced re-runs).

---

## 6. Self-Translation Bypass Policy

To avoid redundant LLM API costs and prevent translation-induced content drift or hallucinations, the runner implements a self-translation bypass policy:

1. **Source Language Detection**:
   - The runner joins `approved_content_record` with `classification_result` on `source_item_id` to retrieve the `primary_language_code` of the source draft.
2. **Bypass Criteria**:
   - If the target `language_code` is identical to `primary_language_code` (e.g. source is `'en'` and target is `'en'`), the runner must **bypass** the LLM API call entirely.
3. **Database Materialization**:
   - The runner directly copies the original `display_title` and `content_body` from `approved_content_record` into `translation_output` for that `language_code`.
   - The upsert fields must be written as:
     - `translation_status = 'completed'`
     - `model_name = 'bypass'`
     - `prompt_version = 'bypass'`
     - `translated_at` = current UTC ISO-8601 timestamp
     - `retry_count = 0`
   - This bypass operation consumes `0` API calls and completes successfully.
