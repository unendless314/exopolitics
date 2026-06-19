# Translate State Transitions

**Document version:** v1.1  
**Updated:** 2026-06-19  
**Status:** Locked Contract  

---

## 1. Translation Workflow States

Every target language translation task for an approved mother-draft moves through distinct workflow states. These states are defined by the `translation_status` column in `translation_output`:

* **`pending`**: The translation task is newly registered or manually queued, awaiting its first execution. This is represented by `translation_status = 'pending'`, or logically if no row exists yet for the `(parent_content_id, language_code)` pair in `translation_output`.
* **`completed`**: The translation has been successfully generated and is up to date. A row exists in `translation_output` with `translation_status = 'completed'` where the stored `source_fingerprint` matches `approved_content_record.content_fingerprint`, and the stored `model_name` and `prompt_version` match the running config.
* **`failed`**: A transient error (timeout, rate limit, parse error, or validation failure) occurred during translation. A row exists with `translation_status = 'failed'`.
* **`stale`**: The upstream mother-draft has been updated (causing a fingerprint mismatch) or the translation configuration (model, prompt version) has changed since translation. A row exists with `translation_status = 'stale'`. This triggers re-translation in the next execution run.

### 1.1 Queue Eligibility vs. Workflow States

To keep state semantics clean, we distinguish between a record's physical state in the database and its eligibility for runner execution:
* **Queue Eligibility**: A translation task is picked up by the runner if it has no row in the database, OR its status is `'pending'`, OR its status is `'stale'`, OR its status is `'failed'` with `retry_count < max_retries` (where `max_retries` is configured in `config/config.yaml`, defaulting to 3) (eligible for retry).
* **Logical Lock (Failed logically)**: If a task is in `'failed'` status and `retry_count >= max_retries`, it is logically locked and will not be selected by the runner. It is excluded from the automatic queue and requires operator override or reset to `'pending'`. (There is no physical `'locked'` string in the `translation_status` column).


---

## 2. State Transition Matrix

The table below defines how a translation record transitions from its **Old State** to a **New State** based on events:

| Old State | Trigger / Event | New State | Translation Output Updates | Side-Effects |
| :--- | :--- | :--- | :--- | :--- |
| **None / Pending** | LLM translation & validation success | **completed** | Insert/Update row (status='completed', retry_count=0, display_title, content, source_fingerprint, translated_at) | Ready for publish export. |
| **None / Pending** | Transient Runner / Validation Failure | **failed** | Insert/Update row (status='failed', retry_count=retry_count+1, display_title=NULL, content=NULL) | Retried in next batch. Columns remain NULL. |
| **failed** (retry < max_retries - 1) | Transient Runner / Validation Failure | **failed** | Update row (status='failed', retry_count=retry_count+1) | Retried in next batch. |
| **failed** (retry = max_retries - 1) | Transient Runner / Validation Failure | **failed** (logically locked) | Update row (status='failed', retry_count=max_retries) | Excluded from automatic queue. |
| **completed** | Upstream mother-draft fingerprint change | **stale** | Update row (status='stale') | Triggers re-translation in next batch. |
| **completed** | Config version shift (`model_name` / `prompt_version`) | **stale** | Update row (status='stale') | Triggers re-translation in next batch. |
| **completed** | Forced Rerun Trigger | **pending** | Update row (status='pending', retry_count=0) | Ready for immediate translation. |
| **completed** | Forced Rerun Failure | **completed** (Unchanged) | None. Rollback database transaction. | Keep old translated outputs unchanged; do not write `failed`. |
| **stale** / **failed** / **failed (logically locked)** | LLM translation & validation success | **completed** | Update row (status='completed', retry_count=0, display_title, content, source_fingerprint, translated_at) | Ready for publish export. |

---

## 3. Invalidation Policy and Change Detection

1. **Fingerprint Comparison**:
   During every runner execution, the orchestrator queries `approved_content_record` and joins it with `translation_output`. If:
   ```text
   approved_content_record.content_fingerprint != translation_output.source_fingerprint
   ```
   The runner must immediately transition that language record's status to `stale` before initiating the LLM call.

2. **Configuration Change Detection**:
   If the running configuration's `model_name` or `prompt_version` differs from the values written in the database record, the runner must transition that record's status to `stale`.

3. **Failed State Safety**:
   If an already `completed` translation is forced to re-run and fails (due to API error or runner-side validation mismatch), the system must **not** overwrite the successful translation with a `failed` or null entry, nor increment the retry count. The transaction must roll back, preserving the previous translation for publishing fallback until a successful rewrite/translation is committed.
   For first-time runs that fail, `display_title` and `content` must remain `NULL` in the database to prevent exposing empty strings or dummy content to downstream modules.
