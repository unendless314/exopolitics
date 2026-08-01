# Translate State Transitions

**Document version:** v1.5  
**Updated:** 2026-08-01  
**Status:** Locked Contract  

---

## 1. Translation Workflow States

Every target language translation task for an approved mother-draft moves through distinct workflow states. These states are defined by the `translation_status` column in `translation_output`:

* **`pending`**: The translation task is newly registered or manually queued, awaiting its first execution. This is represented by `translation_status = 'pending'`, or logically if no row exists yet for the `(parent_content_id, language_code)` pair in `translation_output`.
* **`completed`**: The translation has been successfully generated and is up to date. A row exists in `translation_output` with `translation_status = 'completed'` where the stored `source_fingerprint` matches `approved_content_record.content_fingerprint`, and either the stored `model_name` and `prompt_version` match the running config, or the row represents a bypassed self-translation with `model_name = 'bypass'` and `prompt_version = 'bypass'`.
* **`failed`**: A transient error (timeout, rate limit, parse error, or validation failure) or a permanent client error (non-429 HTTP 4xx) occurred during translation. A row exists with `translation_status = 'failed'`.
* **`stale`**: The upstream mother-draft has been updated (causing a fingerprint mismatch) or the translation configuration (model, prompt version) has changed since translation. A row exists with `translation_status = 'stale'`. This triggers re-translation in the next execution run.

### 1.1 Queue Eligibility vs. Workflow States

To keep state semantics clean, we distinguish between a record's physical state in the database and its eligibility for runner execution:
* **Queue Eligibility**: A translation task is picked up by the runner if it has no row in the database, OR its status is `'pending'`, OR its status is `'stale'`, OR its status is `'failed'` with `retry_count < retry_attempts` (where `retry_attempts` is configured in `config/model_settings.yaml`, defaulting to 3) (eligible for retry).
* **Logical Lock (Failed logically)**: If a task is in `'failed'` status and `retry_count >= retry_attempts`, it is logically locked and will not be selected by the runner. It is excluded from the automatic queue and requires operator override or reset to `'pending'`. (There is no physical `'locked'` string in the `translation_status` column).
* **Immediate Lock on Permanent Failure**: A permanent (non-retryable) client error — any non-429 HTTP 4xx — writes the `failed` row with `retry_count = retry_attempts` in a single execution, reaching the logical lock immediately instead of incrementing one step per run (EXECUTION_POLICY.md section 4). Identical re-requests cannot fix a request/contract problem, so the automatic queue must not attempt them; an operator can rerun the item explicitly via `--force`. The lock pins only the unchanged request: if the upstream content fingerprint changes, fingerprint stale detection (section 3.1, status-agnostic) transitions the locked row to `stale`, and the next bulk run retries it automatically without `--force` — the retried request carries the new content, so it is no longer an identical re-request. (A config version shift alone does not release a locked `failed` row; the configuration stale check applies to `completed` rows only.)


---

## 2. State Transition Matrix

The table below defines how a translation record transitions from its **Old State** to a **New State** based on events:

| Old State | Trigger / Event | New State | Translation Output Updates | Side-Effects |
| :--- | :--- | :--- | :--- | :--- |
| **None / Pending** | LLM translation & validation success | **completed** | Insert/Update row (status='completed', retry_count=0, display_title, summary_short, bullet_1, bullet_2, bullet_3, source_fingerprint, translated_at) | Ready for publish export. |
| **None / Pending** | Transient Runner / Validation Failure | **failed** | Insert/Update row (status='failed', retry_count=retry_count+1, display_title=NULL, summary_short=NULL, bullet_1=NULL, bullet_2=NULL, bullet_3=NULL) | Retried in next batch. All five content columns remain NULL. |
| **failed** (retry < retry_attempts - 1) | Transient Runner / Validation Failure | **failed** | Update row (status='failed', retry_count=retry_count+1) | Retried in next batch. |
| **failed** (retry = retry_attempts - 1) | Transient Runner / Validation Failure | **failed** (logically locked) | Update row (status='failed', retry_count=retry_attempts) | Excluded from automatic queue. |
| **None / Pending / stale / failed** (any eligible state) | Permanent client error (non-429 HTTP 4xx) | **failed** (logically locked) | Insert/Update row (status='failed', retry_count=retry_attempts; first-run content stays NULL, later-run content preserved) | Excluded from automatic queue immediately after a single request; operator `--force` required to rerun the unchanged request. An upstream fingerprint change releases the lock via stale marking (see next row). |
| **failed** (logically locked) | Upstream mother-draft fingerprint change | **stale** | Update row (status='stale') | Re-enters the automatic queue; the next bulk run retries automatically without `--force`, because the retried request carries the new content. A config version shift alone does not release a locked failed row (the configuration stale check targets `completed` rows only). |
| **completed** | Upstream mother-draft fingerprint change | **stale** | Update row (status='stale') | Triggers re-translation in next batch. |
| **completed** | Config version shift (`model_name` / `prompt_version`) | **stale** | Update row (status='stale') | Triggers re-translation in next batch. **Exception**: Rows with `model_name = 'bypass'` are exempt and remain `completed`. |
| **completed** (fresh: fingerprint & config match) | Forced Rerun Trigger (`--force`) | **completed** (unchanged until success) | None. Forced rerun is non-persisted: the row is never rewritten to `pending` before or during the API call. | In-memory forced execution; the existing row stays publishable throughout the attempt. |
| **completed** (fresh) | Forced Rerun Success | **completed** | Single short transaction atomically overwrites the five content fields, `source_fingerprint`, `translated_at`; `retry_count=0`. | Ready for publish export. |
| **completed** (fresh) | Forced Rerun Failure / Interruption | **completed** (Unchanged) | None. No DB write occurs (no `failed` write, no `retry_count` increment); the transaction is rolled back. | Keep old translated outputs unchanged and publishable. |
| **stale** | Forced Rerun Trigger (`--force`) | (normal stale retry path) | A stale row is not the latest completed artifact, so the forced-rerun protection does not apply; it is processed exactly like a normal stale retry. | On failure the row transitions to `failed` with `retry_count` incremented per standard policy. |
| **stale** / **failed** / **failed (logically locked)** | LLM translation & validation success | **completed** | Update row (status='completed', retry_count=0, display_title, summary_short, bullet_1, bullet_2, bullet_3, source_fingerprint, translated_at) | Ready for publish export. |

---

## 3. Invalidation Policy and Change Detection

1. **Fingerprint Comparison**:
   During every runner execution, the orchestrator queries `approved_content_record` and joins it with `translation_output`. If:
   ```text
   approved_content_record.content_fingerprint != translation_output.source_fingerprint
   ```
   The runner must immediately transition that language record's status to `stale` before initiating the LLM call. This fingerprint validation applies equally to bypassed self-translations (triggering a re-evaluation of bypass status in the next run).

2. **Configuration Change Detection**:
   If the running configuration's `model_name` or `prompt_version` differs from the values written in a **`completed`** database record, the runner must transition that record's status to `stale`. This check applies to completed, non-bypass rows only: rows in other states (`pending`, `failed`, `stale`) are either already queue-eligible or, for a logically locked `failed` row, remain locked until the upstream content fingerprint changes (section 1.1). **Exception**: Translation rows representing bypassed self-translations (where `model_name = 'bypass'` and `prompt_version = 'bypass'`) are exempt from this configuration stale check and remain in `completed` status.

3. **Failed State Safety**:
   A forced rerun of a fresh `completed` row (source fingerprint and config still matching) is a non-persisted execution mode: the runner never writes an intermediate `pending` state and never holds a database transaction across the API call. Only after a successful API response and validation does a single short transaction atomically overwrite the five content fields. If the rerun fails (API error, runner-side validation mismatch, cancellation, or process interruption), the system must **not** overwrite the successful translation with a `failed` or null entry, nor increment the retry count; the previous translation remains intact and publishable until a successful rewrite is committed.
   If stale detection has already marked the row `stale` (source or config changed), that row is no longer the latest completed artifact: even under `--force` it follows the normal stale retry path, and a failure writes `failed` with `retry_count` incremented; the old content is not presented as the current completed result.
   For first-time runs that fail, all five content fields (`display_title`, `summary_short`, `bullet_1`, `bullet_2`, `bullet_3`) must remain `NULL` in the database to prevent exposing empty strings or dummy content to downstream modules.
   Whether old content retained in `stale` or `failed` rows may still serve as a publish fallback is the `publish` module's consumption contract and is defined and verified there, not by translate tests.
