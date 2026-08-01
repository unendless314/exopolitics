# Translate Execution Policy

**Document version:** v1.5  
**Updated:** 2026-08-01  
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
  4. A row exists in `translation_output` with `translation_status = 'failed'` and `retry_count < retry_attempts` (where `retry_attempts` is defined in `config/model_settings.yaml`).
* **Batch Size (source-item unit)**: `batch_size` counts **distinct source items (mother-drafts)**, not language tasks. The runner first selects up to `batch_size` distinct `parent_content_id` values that have at least one eligible translation, ordered by `approved_at ASC` with `parent_content_id ASC` as the deterministic tie-breaker, then expands **all** eligible target-language tasks of each selected article. A batch boundary must never split one article's pending language set; an article's languages that are already completed (and not stale) are not redone. This matches the operator mental model of "this run processed N articles"; API concurrency and request frequency are governed separately by `max_concurrent_requests` and `rate_limit_per_minute`. Defaults to the value configured in `config/model_settings.yaml`, overridable via the `--batch-size` CLI flag. The effective value must be a **positive integer**: the CLI rejects non-positive `--batch-size` overrides at option parsing (usage error), config validation enforces `execution_policy.batch_size > 0`, and the orchestrator re-validates the effective value before any queue loading, stale marking or API traffic. The orchestrator boundary requires an actual `int`: non-integer numerics such as `1.5` from a direct Python caller are rejected with an input error, not silently truncated.
* **Run Summary**: The run summary must report both counts: the number of selected source items and the number of resulting language tasks queried.
* **Dry Run / Preview**: If `--preview-prompts` is supplied:
  * The runner prepares the inputs, constructs the prompts for each target language, and prints the generated payloads to stdout.
  * It must **not** invoke the LLM API and must **not** write any entries to the database.
  * Because it performs no API calls and no DB writes, it does not acquire the multi-process runner lock.
* If `--dry-run` is supplied:
  * The runner executes the complete translation flow **including real LLM API requests**, but must not commit any database writes (stale detection marking included).
  * Because it consumes real API quota, it must acquire the same multi-process runner lock as a normal run, so a dry-run and a normal run (or two dry-runs) can never execute duplicate API requests for the same queue concurrently.

---

## 3. Database Transactions & Concurrency

* **Multi-Process Runner Lock**:
  * To prevent duplicate API execution and lock contention in SQLite, the runner must acquire an exclusive file lock on `data/translate_runner.lock` at start. This applies to normal runs and to `--dry-run` (which still issues real LLM API requests); `--preview-prompts` is exempt because it makes no API calls.
  * If the lock cannot be acquired, the runner must log an error and exit immediately.
* **Concurrency Semaphore**:
  * Parallel execution of translation requests is managed asynchronously. Concurrency is limited by `max_concurrent_requests` (via `asyncio.Semaphore`, defaulting to the value configured in `config/model_settings.yaml`, e.g. 5) to respect API rate limits.
* **Isolation of Network Calls**:
  * All LLM API translation calls must be executed **outside** database transactions. Holding database connections or write transactions open during network calls is strictly forbidden.
* **Granular Database Transactions**:
  * The transaction must only wrap the final write operations for a single translated item.
  * For each successfully translated language target:
    1. Acquire an `asyncio.Lock` to serialize database write access in the event loop.
    2. Open a short write transaction block using `BEGIN IMMEDIATE`.
    3. Upsert the row in `translation_output` with `translation_status = 'completed'`, updating fields like `translated_at`, `display_title`, `summary_short`, `bullet_1`, `bullet_2`, `bullet_3`, `source_fingerprint`, and resetting `retry_count` to 0.
    4. Commit and close the transaction immediately.

---

## 4. Error Handling & Retry Policies

* **Transient Error Trapping**:
  * Network timeouts, rate limits (`429`), server errors (`5xx`, e.g. model overloaded `503`), JSON parsing/schema validation, or runner-side validation failures must not crash the overall translation runner.
  * **Retry eligibility by HTTP status**: only `429` and `5xx` responses (plus transport-level timeouts/network errors) are eligible for retry. Any other non-200 status — including general HTTP `4xx` client errors such as `400`, `401`, `403`, `404` — is a permanent failure: the runner must fail the task immediately after the first response, without consuming the retry budget or sleeping. Because an identical re-request cannot fix a request/contract problem, the failed task is also locked out of the automatic queue immediately: the runner writes the `failed` row with `retry_count` set to `retry_attempts` (the same logical lock as an exhausted retry budget; STATE_TRANSITIONS.md section 1.1), and rerunning the unchanged request requires explicit operator intervention (`--force` on the specific item). The lock pins only the unchanged request: if the upstream `content_fingerprint` later changes, fingerprint stale detection transitions the locked row to `stale` and the next bulk run retries it automatically, because the retried request carries new content and is no longer an identical re-request. A provider-specific retryable `4xx` may only be added together with provider documentation, implementation, and a regression test in the same change.
  * When a transient (retryable) error occurs during a translation task:
    - The runner must catch the error.
    - Write a `'failed'` status in `translation_output` for `(parent_content_id, language_code)`. Keep all five content fields (`display_title`, `summary_short`, `bullet_1`, `bullet_2`, `bullet_3`) as `NULL` if this is the first execution (do not write empty strings or fake content).
    - Increment `retry_count` by 1.
    - If `retry_count >= retry_attempts` (where `retry_attempts` is configured in `config/model_settings.yaml`, e.g. 3), the translation task is logically locked (excluded from automatic retries) and requires operator intervention.
* **Exponential Backoff**:
  * Implement exponential backoff (utilizing the backoff factor configured in `config/model_settings.yaml`, e.g. 2.0 -> 2s, 4s, 8s) between retries during API execution to respect provider rate limits.
* **Operator Forced Re-run Error Handling**:
  * If a manual/operator-forced re-run is triggered for an already `completed` translation, any execution or validation failure must **not** overwrite the existing successful translation or increment the retry counter. The runner must rollback the database transaction, leaving the existing translated five-field content unchanged in the database, and log the failure to stderr.

---

## 5. Runner-Side Content Validation Rules

To guarantee structured field fidelity and translation quality, the runner must execute the following content validation after receiving the LLM structured JSON response:

1. **Title Length Check**:
   - Title length limits are enforced per target language, including the Japanese limit of 120 double-byte characters (limits are canonically configured per-language in `config/model_settings.yaml`).
   - If the translated title exceeds the configured limit for the target language, the validation fails.
2. **Aggregate Content Length Ratio Check**:
   - Aggregate all non-empty translated fields (`translated_summary` plus all non-null translated bullets) and the corresponding source fields (`summary_short` plus all non-null source bullets), then calculate: `len(aggregated translation) / len(aggregated source)`.
   - If the ratio is strictly greater than the configured `content_ratio_limit` (configured in `config/model_settings.yaml`, currently `5.0`), the validation fails. (This prevents LLM hallucination or excessive rambling.)
   - The ratio must always be computed over the aggregate, never over a single short bullet, to avoid false rejections of valid translations.
3. **Script Presence Check**:
   - For target `language_code = 'zh'`, the aggregated translated content must contain at least one CJK Unified Ideograph (Chinese character).
   - For target `language_code = 'ja'`, the aggregated translated content must contain at least one Hiragana or Katakana character.
   - Proper nouns and acronyms are permitted to remain in English; this check only verifies the presence of the target script.
4. **Nullability Shape Match**:
   - The source/response nullability shapes must match exactly: for each bullet slot, a `NULL` source bullet requires a `NULL` translated bullet, and a non-empty source bullet requires a non-empty translated bullet.
   - Partially populated bullet combinations in the response are rejected.
5. **Migration-Period Label Guard (`zh` / `ja` only)**:
   - For `translated_summary` and every non-null translated bullet: after stripping leading whitespace and any optional Markdown emphasis or list markers, if the value starts with a known UI presentation label followed by a colon, the validation fails.
   - The guard list contains the three English labels (`Key Claim`, `Evidence Level`, `Objective Impact`) plus all observed zh/ja label variants documented in [TRANSLATION_LABEL_LEAKAGE.md](../../../known_issues/resolved/TRANSLATION_LABEL_LEAKAGE.md) Section 4.2:
     - zh: `主要主張`, `關鍵主張`, `核心主張`, `證據層級`, `證據等級`, `客觀影響`, `實際影響`
     - ja: `主要な主張`, `主要主張`, `主張の要点`, `証拠の水準`, `証拠レベル`, `証拠水準`, `エビデンスレベル`, `客観的な影響`, `客観的影響`, `目的上の影響`
   - This guard only detects erroneous presentation-string backflow into content. It is not the primary correctness mechanism, and global string replacement on content is forbidden.
6. **Validation Mismatch Treatment**:
   - Any validation failure is treated as a transient runner error.
   - It triggers the same state updates as an API error (status = `'failed'`, `retry_count` increments, rollback on forced re-runs).

---

## 6. Self-Translation Bypass Policy

To avoid redundant LLM API costs and prevent translation-induced content drift or hallucinations, the runner implements a self-translation bypass policy:

1. **Source Language Detection**:
   - The runner relies solely on `approved_content_record.content_language_code` to determine the language of the finalized mother-draft. It does not query `classification_result` or other upstream tables.
2. **Bypass Criteria**:
   - If the target `language_code` is identical to `approved_content_record.content_language_code` (e.g. both are `'en'`), the runner must **bypass** the LLM API call entirely.
3. **Database Materialization**:
   - The runner directly copies the five content fields (`display_title`, `summary_short`, `bullet_1`, `bullet_2`, `bullet_3`) from `approved_content_record` into `translation_output` for that `language_code`.
   - The upsert fields must be written as:
     - `translation_status = 'completed'`
     - `model_name = 'bypass'`
     - `prompt_version = 'bypass'`
     - `translated_at` = current UTC ISO-8601 timestamp
     - `retry_count = 0`
   - This bypass operation consumes `0` API calls and completes successfully.
4. **Invalidation Exceptions**:
   - Bypassed rows are exempt from configuration-driven stale checks. Changing the execution config's `model_name` or `prompt_version` does not invalidate bypassed rows.
   - Bypassed rows remain subject to standard content fingerprint validation. If `approved_content_record.content_fingerprint != translation_output.source_fingerprint` (indicating the mother-draft was edited), the record transitions to `stale` to trigger bypass re-evaluation.
