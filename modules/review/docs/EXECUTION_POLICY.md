# Review Execution Policy

**Document version:** v1.2  
**Updated:** 2026-06-15  
**Status:** Planning & Active rewrite draft

---

## 1. Purpose

This document defines execution controls, transaction boundaries, rate-limiting, and error-handling behaviors for the `review` module runner.

---

## 2. Queue Selection & Batching

* **Selection criteria:** The orchestrator runs in batches. It selects items from `classification_result` that lack matching records in the `review_decision` table, **OR** items where the previous review status was `'failed'` and the `retry_count` is strictly less than `3`.
* **Batch size:** Defaults to `20` items per run (configurable via `--batch-size`).
* **Dry Run / Preview:** If `--preview-prompts` is supplied:
  * The script fetches pending items, runs the prompt constructor, and prints the generated prompt payloads to stdout.
  * It must **not** invoke the LLM API and must **not** write any entries to the database.

---

## 3. Database Transactions & Concurrency

* **Strict Transaction Boundaries:** All database operations (fetching, locking, writing) must run within a strict transaction block using SQLite's `BEGIN IMMEDIATE` to prevent concurrency issues.
* **Isolation of Writes:** For each item evaluated:
  * Write the `review_decision` row. If the item was previously in a `'failed'` state, update the row and preserve or increment the `retry_count` depending on the outcome.
  * If the decision is `approved`, write matching records into `editor_brief` and `review_output` tables.
  * Commit the transaction only when all writes succeed. If any write fails, roll back the transaction for that item.
* **Idempotency:** Re-running the queue must not duplicate rows. The repository must use `ON CONFLICT(source_item_id) DO UPDATE` constraints to ensure safe, repeatable updates.

---

## 4. Runner-Side Error Handling & Retry Policies

* **Transient Failures:** Network timeouts, model overload (`503`), rate-limiting (`429`), or JSON parsing failures must not crash the orchestrator execution.
* **Failed State Persistence:**
  * When the LLM client or parsing schema validation raises an exception, the runner must trap the exception and persist a `'failed'` status in `review_decision` for that `source_item_id`.
  * **Important Downstream Action Value:** When writing a `'failed'` status, the runner must set `downstream_action` to **`NULL`**. This satisfies the database CHECK constraint:
    `CHECK ((review_status = 'failed' AND downstream_action IS NULL) OR (review_status IN ('approved', 'rejected') AND downstream_action IS NOT NULL))`
  * The runner must write a clear error message or traceback snippet to `decision_reason` and **increment** the `retry_count` by `1` (or set to `1` on first failure).
  * If an item's `retry_count` reaches `3`, it is locked out of the automatic queue and will no longer be selected, requiring manual intervention or an admin override.
* **Graceful Backoff:** Implement an exponential backoff delay (e.g. 2s, 4s, 8s) between retries during API execution to respect the provider's rate limits.
