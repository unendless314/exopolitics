# Curation Execution Policy

**Document version:** v1.9  
**Updated:** 2026-07-30  
**Status:** Planning & Active rewrite draft

---

## 1. Purpose

This document defines execution controls, transaction boundaries, rate-limiting, and error-handling behaviors for the `curate` module runner.

---

## 2. Queue Selection & Batching

* **Selection criteria:** The orchestrator runs in batches. It selects items from `classification_result` that lack matching records in the `curation_decision` table, **OR** items where the previous curation status was `'failed'` and the `retry_count` is strictly less than `3`.
* **Batch size:** Defaults to `20` items per run (configurable via `--batch-size`).
* **Dry Run / Preview:** If `--preview-prompts` is supplied:
  * The script fetches pending items, runs the prompt constructor, and prints the generated prompt payloads to stdout.
  * It must **not** invoke the LLM API and must **not** write any entries to the database.

---

## 3. Database Transactions & Concurrency

* **Multi-Process Runner Lock (SQLite Coordination):** Since SQLite is a local file-based database, running multiple instances of `curate run` in separate processes simultaneously can lead to lock contention and duplicate work. The orchestrator must acquire an exclusive file lock on `data/curate_runner.lock` at start. If the lock is held, the runner must exit with an error.
* **Internal Concurrency & Semaphore:** Within the single runner process, parallel execution is achieved asynchronously using a task queue. Concurrency is limited by `max_concurrent_requests` (via `asyncio.Semaphore`).
* **Dispatch-Time Rate Pacing:** A shared dispatch pacer spaces the start of every HTTP request to the LLM provider at least `60 / rate_limit_per_minute` seconds after the previous dispatch. Pacing is enforced at request dispatch time (inside the semaphore) and covers retry attempts as well as initial attempts; it must not be implemented as pre-scheduled worker staggering, because the semaphore caps in-flight requests but cannot prevent queued workers from dispatching back-to-back after a slow request releases a slot. The pacer holds its lock through each wait and anchors the next slot to the moment the wait actually completes, so event-loop stalls that wake several workers late in the same loop iteration cannot collapse the spacing into a burst. After an idle gap the schedule likewise re-anchors to the current time rather than catching up on stale slots.
* **Isolation of Network Calls:** LLM API requests must be performed **outside** of any database transaction. Doing network calls inside a transaction is strictly forbidden as it keeps SQLite write locks active for seconds, blocking other reads/writes.
* **Granular Database Transactions:** The database transaction must only wrap the final write operations. For each item:
  1. Acquire an `asyncio.Lock` to serialize SQLite writes within the async loop.
  2. Start a short transaction block using `BEGIN IMMEDIATE`.
  3. Write the `curation_decision` row (handling updates/upserts).
  4. Perform the conditional writes or deletions for `editor_brief` and `curation_output` depending on the resolved `downstream_action` (as defined in the validation matrix and `STATE_TRANSITIONS.md`).
  5. Commit and release the transaction immediately.
* **Idempotency:** Re-running the queue must not duplicate rows. The repository must use `ON CONFLICT(source_item_id) DO UPDATE` constraints to ensure safe, repeatable updates.

---

## 4. Runner-Side Error Handling & Retry Policies

* **Retryable (Transient) Failures:** Network errors, request timeouts, rate-limiting (`429`), server-side errors (any `5xx`), JSON parsing failures, malformed provider envelopes (missing/non-object `choices` or `message` structures), and response schema validation failures must not crash the orchestrator execution. They are retried up to `retry_attempts` per item with exponential backoff.
  * **Schema Validation Failure Retry (Decided):** A response that parses as JSON but fails the local curation response contract (missing fields, wrong types, or validation-matrix violations) is deliberately treated as retryable. With a non-zero sampling temperature, the next attempt re-samples the model output and may satisfy the contract. A systematically broken prompt or schema will still exhaust the retry limit and lock the item out of the automatic queue; that is an accepted cost.
* **Non-Retryable Failures:** General HTTP `4xx` statuses other than `429` (e.g. `400`/`401`/`403`/`404`, indicating malformed requests or credential problems) and explicit model refusals must **not** be retried. The request fails fast after a single attempt, and the item follows the same failed-state persistence rules below with a readable failure reason (including the HTTP status code where applicable).
* **Failed State Persistence (Workflow Scope Boundary):**
  * **Normal Queue/Failed Item Runs:** When processing pending or failed items, if the LLM client or parsing schema validation raises an exception, the runner must trap the exception, write a `'failed'` status in `curation_decision` for that `source_item_id` (setting `downstream_action` to `NULL` to satisfy the DB constraint), record the error message in `decision_reason`, and increment the `retry_count` by 1. Once `retry_count` reaches 3, the item is locked out of the automatic queue.
  * **Operator-Forced Re-runs of Completed Items:** If an item is already in a completed state (`approved` or `rejected`) and a manual re-run is forced, any execution failure (e.g. LLM timeout, API exception, parser error) must **not** overwrite the existing successful/rejected status or increment the retry counter. Instead, the runner must rollback the transaction completely, preserving the old curation results unchanged in the database, and log the failure to stderr.
* **Graceful Backoff:** Implement an exponential backoff delay (e.g. 2s, 4s, 8s) between retries during API execution to respect the provider's rate limits.
