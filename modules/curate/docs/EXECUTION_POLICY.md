# Curation Execution Policy

**Document version:** v1.5  
**Updated:** 2026-06-16  
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

* **Transient Failures:** Network timeouts, model overload (`503`), rate-limiting (`429`), or JSON parsing failures must not crash the orchestrator execution.
* **Failed State Persistence (Workflow Scope Boundary):**
  * **Normal Queue/Failed Item Runs:** When processing pending or failed items, if the LLM client or parsing schema validation raises an exception, the runner must trap the exception, write a `'failed'` status in `curation_decision` for that `source_item_id` (setting `downstream_action` to `NULL` to satisfy the DB constraint), record the error message in `decision_reason`, and increment the `retry_count` by 1. Once `retry_count` reaches 3, the item is locked out of the automatic queue.
  * **Operator-Forced Re-runs of Completed Items:** If an item is already in a completed state (`approved` or `rejected`) and a manual re-run is forced, any execution failure (e.g. LLM timeout, API exception, parser error) must **not** overwrite the existing successful/rejected status or increment the retry counter. Instead, the runner must rollback the transaction completely, preserving the old curation results unchanged in the database, and log the failure to stderr.
* **Graceful Backoff:** Implement an exponential backoff delay (e.g. 2s, 4s, 8s) between retries during API execution to respect the provider's rate limits.
