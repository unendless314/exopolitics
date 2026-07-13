# Classification Execution Policy

**Document version:** v3.2  
**Updated:** 2026-06-12  
**Status:** Planning & Active rewrite draft

---

## 1. Purpose

This document defines execution controls, batching, rate-limiting, retry behavior, and transactional boundaries for the `classify` module.

---

## 2. Queue & Batching Controls

To avoid overloading API limits and database connections, classification runs in bounded batches.

* **Batch Size:** The maximum number of pending items pulled from the database in a single CLI run (default: `20` items).
* **Concurrency Limit:** The maximum number of concurrent HTTP requests sent to the LLM provider (default: `3` concurrent requests controlled via `asyncio.Semaphore`).
* **Rate Limiting:** Maximum model requests per minute (default: `60`).
* **Queue Selection:** The pending queue query must filter strictly on `t.text_processing_status = 'completed'` (jointly with `s.ingest_status = 'ingested'`). Items with a status of `low_context` or `failed` must be bypassed during item selection and excluded from enter-stage classification processing.

---

## 3. Concurrency and Idempotency Policy (MVP Restriction)

### Single-Worker Assumption
* **MVP Rule:** The `classify` execution loop is designed and assumed to run as a **single-worker process** (e.g., a single cron job, single systemd timer, or a single execution worker).
* **Concurrency Boundary:** The database pending query (`LEFT JOIN classification_result ... WHERE c.classification_result_id IS NULL`) does **not** lock rows. Running multiple concurrent `classify` runner processes will result in duplicate LLM API submissions and redundant token consumption, though the database unique constraint (`source_item_id UNIQUE`) will safely reject duplicate writes at the storage layer.

### Future Multi-Worker Scaling (Post-MVP)
If the system must scale to multiple concurrent classification workers in the future, we must introduce a **task-claiming mechanism** to ensure items are claimed before they are sent to the LLM. Potential implementations include:
* Using database-supported transactional claiming or locking semantics during item selection.
* Using a dedicated job/claim table to track leases.
* Adding status fields to track active processing states.
* Using a separate task queue system.

---

## 4. Error Handling and Retry Strategy

Network calls to LLM APIs can fail transiently. The execution loop must handle these gracefully.

### Retry Rules
1. **Eligible Errors:** Network timeouts, HTTP `429` (Rate Limit Exceeded), and HTTP `5xx` server errors are eligible for retry.
2. **Max Retry Attempts:** `3` attempts per item.
3. **Backoff:** Exponential backoff with a factor of `2.0` (e.g., wait 2s, then 4s, then 8s) plus slight random jitter.
4. **Invalid JSON/Schema Mismatch:** If the LLM returns invalid JSON or fails the schema contract, it is treated as a classification error and retried.

### Final Execution Failure
If an item fails all retries:
* **No Database Record:** Do **not** write a row to the `classification_result` table for that item.
* **Keep Pending:** Leaving the item without a classification result ensures it remains in the pending queue to be re-attempted on the next run.
* **Failure Isolation:** An execution failure for one item must not fail the entire run. Other items in the batch must continue processing.

---

## 5. Transaction Boundaries

Database updates must remain atomic and safe from connection dropouts.

* **Item-Level Transactions:** Each classification decision must be saved in its own transaction (or savepoint). If writing a single item result fails (e.g., due to a constraint or lock), it must roll back only that item's state and continue saving other successful classifications.
* **Idempotency:** Within a single worker execution thread, there is no risk of duplicate item processing since items are processed sequentially or collected cleanly from the initial query.
