# Execution Policy

**Document version:** v2.1  
**Updated:** 2026-06-05  
**Status:** Active

---

## 1. Purpose

This document defines how `classify` processes work during a CLI run.

---

## 2. Configured Parameters

The following values are owned by `modules/classify/config/model_settings.yaml`:

* `batch_size`
* `max_concurrent_requests`
* `rate_limit_per_minute`
* `retry_attempts`
* `backoff_factor`
* `request_timeout_seconds`
* `min_context_characters`
* `active_prompt_template`
* provider/model selection settings

These parameters are not exposed as CLI override flags in the MVP.

Provider credentials are resolved from environment variables named by the provider's `api_key_env` setting.

For local development, the CLI may load a workspace root `.env` file before command execution.

For cloud or production environments, secrets should be injected directly into the process environment by the hosting platform.

---

## 3. Batch Behavior

* The module selects up to `batch_size` pending items at a time.
* Items may be processed concurrently up to `max_concurrent_requests`.
* Rate limiting must respect `rate_limit_per_minute`.
* A single CLI run stops after the current batch has been processed or skipped.

---

## 4. Per-Item Execution Flow

For each pending item:

1. read `title` and `summary`
2. run deterministic low-context checks
3. if the item is obviously too thin, write `topic_class = 'unknown'`
4. otherwise call the LLM
5. validate the structured output
6. persist the result transactionally

---

## 5. Retry And Timeout Rules

* Each LLM request uses `request_timeout_seconds`.
* Failed requests may be retried up to `retry_attempts`.
* Backoff uses exponential growth: `backoff_factor * (2 ** attempt_number)`.

### 5.1 Retryable Failures

A request is retried when any of the following occur (up to `retry_attempts`):

* **Network Timeout:** The request does not complete within `request_timeout_seconds`.
* **Provider Outages:** HTTP error statuses such as 500, 502, 503, or 504.
* **Rate Limits:** HTTP status 429 (Too Many Requests).
* **Malformed output:** The API call succeeded, but the output failed validation (see Section 5.2).

### 5.2 Parser and Validator Contract

The parser must validate the LLM response. The following errors are treated as **malformed output** and will trigger a retry:

* **JSON Parse Error:** The output is not valid JSON (after stripping wrapping markdown backticks, if present).
* **Missing Mandatory Keys:** The parsed JSON does not contain all four required keys: `topic_class`, `classification_confidence`, `edit_candidate`, and `classification_reason`.
* **Invalid Enum Value:** The `topic_class` value is not one of: `'core'`, `'adjacent'`, `'irrelevant'`, or `'unknown'`.
* **Out-of-Bounds Confidence:** The `classification_confidence` is not a numeric value between `0.0` and `1.0` inclusive.
* **Invalid Edit Flag:** The `edit_candidate` is not exactly `0` or `1` (or a boolean that maps to it).

---

## 6. Failure Handling

If an item still fails after all retries (e.g. consistently timeout, consistently rate-limited, or model outputs persistent invalid JSON):

* log the error details and the specific failure cause (e.g. `'JSONDecodeError'` or `'ValidationError'`)
* do not write a `classification_result` row
* continue processing the remaining items in the batch

This keeps the item eligible for a retry in subsequent runs without adding workflow-tracking database columns to the MVP schema.

---

## 7. Progress Reporting

During execution, the CLI should emit line-based progress updates suitable for both humans and non-interactive logs.

Preferred behavior:

* write progress to `stderr`
* report batch counts and completion counts
* avoid TTY-only progress bar behavior as a hard dependency

---

## 8. Optional Diagnostic Report

The module may provide an optional read-only command such as `classify export-report`.

Its purpose is diagnostic visibility rather than core pipeline execution.

Useful report contents may include:

* total classified item count
* breakdown by `topic_class`
* average confidence for LLM-produced results
* filtering for `edit_candidate = 1`
* highlighting of edge cases such as low-confidence `core` items or `unknown` items

If implemented, this command should:

* read from the canonical database without modifying state
* write a standalone static HTML file or equivalent diagnostic artifact
* remain optional and non-blocking for MVP completion
