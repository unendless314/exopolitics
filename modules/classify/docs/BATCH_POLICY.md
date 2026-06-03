# Classification Batch & Concurrency Policy

**Document version:** v1.0  
**Updated:** 2026-06-03  
**Status:** Concrete Specification

---

## 1. Batch Execution Boundaries

The classify pipeline processes items in discrete execution batches to manage database connections and API quotas. The parameters are loaded strictly from `config/model_settings.yaml`:

* **Batch Size (`batch_size`):** Default **20** pending `source_item` rows from SQLite. Limits memory footprint and transaction locks.
* **Concurrency (`max_concurrent_requests`):** Default **3** concurrent LLM API calls using Python's `asyncio`.
* **Rate Limits (`rate_limit_per_minute`):** Default **60** API requests per minute.

---

## 2. LLM Call Retries and Backoff

If an LLM call fails (e.g., API down, rate limited, network timeout):
* **Retries:** Default **3** retry attempts (configured as `retry_attempts`).
* **Backoff:** Exponential backoff: `delay = backoff_factor * (2 ** attempt_number)`. Default `backoff_factor` is **2.0** seconds.
* **Partial Failures:** If a single feed item fails classification after all retries, the orchestrator logs the error, skips it, and continues with the rest of the batch. The failed item will not have a row in `classification_result` and will be picked up in subsequent batch executions.

---

## 3. SLA and Queue Retention Rules

* **Processing Timeout:** Default **10.0** seconds (configured as `request_timeout_seconds`). If it exceeds this, it is treated as a timeout error and retried.
* **Batch Lifetime:** A single CLI invocation of `classify run` will terminate if it cannot make progress after all items in the current batch have been processed or skipped.
* **Cleanup & Retries for Failed Items:** If an item consistently fails to classify (e.g., due to toxic content flags or unparseable metadata), the `review` module can triage the item or assign a default classification class (such as `'irrelevant'`).
