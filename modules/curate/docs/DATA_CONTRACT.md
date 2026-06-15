# Curate Data Contract

**Document version:** v1.4  
**Updated:** 2026-06-16  
**Status:** Planning & Active rewrite draft

---

## 1. Purpose

The `curate` module records its outputs in three distinct tables in `data/canonical.db` to maintain strict boundaries and flexibility:
1. **`curation_decision`**: Stores the workflow-specific outcome of the curation run.
2. **`editor_brief`**: Stores non-public editorial analysis and caution notes (handoff data for a future human editor or rewrite module).
3. **`curation_output`**: Stores immediately publishable metadata and summaries (safe for the site or export layers to consume).

### Design Constraints
* **One-to-One Relationships:** For each `source_item`, there is at most one record in each of the three tables.
* **Conditional Row Creation:** 
  * `curation_decision` is created for every attempted item.
  * `editor_brief` is required and generated for any item with `downstream_action` equal to `'publish_link'`, `'publish_summary'`, or `'edit_rewrite'`. For `'reject_discard'` or `'failed'` items, it is omitted.
  * `curation_output` is required and generated for any item with `downstream_action` equal to `'publish_link'` or `'publish_summary'`. For `'edit_rewrite'`, `'reject_discard'`, or `'failed'` items, it is omitted.
* **Cascading Deletions:** All tables must have a foreign key to `source_item(source_item_id)` with `ON DELETE CASCADE`.
* **Zero Modification on Upstream:** `curate` must not write to or alter the `source_item` or `classification_result` tables.
* **Separation from Edit module:** `curate` does not write directly to `edit_draft`. The `editor_brief` serves as the interface boundary.
* **Runner-Generated Failed State:** The `failed` status in `curate_status` is generated strictly by the runner/orchestrator upon catching transient API or parser errors, never by the model.
* **Downstream Action Nullability:** If `curate_status = 'failed'`, the `downstream_action` column **must be `NULL`** since no decision could be reached. If `curate_status` is `'approved'` or `'rejected'`, `downstream_action` **must be `NOT NULL`**.
* **Retry Counter:** Failed items can be retried up to 3 times before requiring manual operator intervention. This is tracked via `retry_count`.

---

## 2. Database Schema

### 2.1 `curation_decision`
Stores workflow-level metadata indicating whether a feed item is approved and its downstream routing.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `curation_decision_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate key. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | FK to `source_item(source_item_id) ON DELETE CASCADE`. |
| `curate_status` | `TEXT` | `NOT NULL` | Outcome state: `'approved'`, `'rejected'`, or `'failed'`. |
| `downstream_action` | `TEXT` | `NULL` | Routing target: `'publish_link'`, `'publish_summary'`, `'edit_rewrite'`, or `'reject_discard'`. Must be `NULL` if `curate_status = 'failed'`, and `NOT NULL` otherwise. |
| `decision_reason` | `TEXT` | `NULL` | Concise reason behind the curation decision or runner exception message. |
| `retry_count` | `INTEGER` | `NOT NULL` | Number of times this curation has failed to complete (default `0`). |
| `model_name` | `TEXT` | `NOT NULL` | Curation LLM model name (e.g., `'gpt-5.4-mini'`). |
| `prompt_version` | `TEXT` | `NOT NULL` | Active curation prompt version. |
| `curated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |

### 2.2 `editor_brief`
Stores editorial analysis and guidelines that describe the item's core parameters without hardcoding frontend-facing text. Required and created for any item where `downstream_action IN ('publish_link', 'publish_summary', 'edit_rewrite')`.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `editor_brief_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate key. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | FK to `source_item(source_item_id) ON DELETE CASCADE`. |
| `brief_goal` | `TEXT` | `NOT NULL` | Core editorial objective. |
| `target_format` | `TEXT` | `NOT NULL` | Prompted formatting targets (e.g., `'structured_summary'`, `'link_card'`). |
| `key_claim` | `TEXT` | `NULL` | Factual claim identified in the article. |
| `key_evidence` | `TEXT` | `NULL` | Quality and category of evidence identified. |
| `required_context` | `TEXT` | `NULL` | Critical institutional context or government involvements. |
| `risk_flags` | `TEXT` | `NULL` | JSON string array of warning tags (e.g., `["clickbait", "speculative_math"]`). |
| `tone_guidance` | `TEXT` | `NOT NULL` | Editorial tone constraints (e.g., `'neutral, factual'`). |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

### 2.3 `curation_output`
Stores structured publishable items generated by the automated curation pipeline, ready to be read by the downstream mock-publish or publish modules. Required and created for any item where `downstream_action IN ('publish_link', 'publish_summary')`.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `curation_output_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate key. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | FK to `source_item(source_item_id) ON DELETE CASCADE`. |
| `display_title` | `TEXT` | `NOT NULL` | De-sensationalized, cleaned title for display. |
| `summary_short` | `TEXT` | `NOT NULL` | One-paragraph summary. For `publish_link` items, this acts as the required excerpt text. |
| `bullet_1` | `TEXT` | `NULL` | First key bullet summary point (NULL for `publish_link` items). |
| `bullet_2` | `TEXT` | `NULL` | Second key bullet summary point (NULL for `publish_link` items). |
| `bullet_3` | `TEXT` | `NULL` | Third key bullet summary point (NULL for `publish_link` items). |
| `source_attribution_note` | `TEXT` | `NULL` | Framing note (e.g., `'Translation from French'`, `'First reported by Politico'`). |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

---

## 3. SQLite DDL

This migration script will reside in `modules/curate/src/migrations/v001_initial_curate_tables.sql`:

```sql
PRAGMA foreign_keys = ON;

-- 1. curation_decision table
CREATE TABLE IF NOT EXISTS curation_decision (
    curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    curate_status TEXT NOT NULL CHECK (curate_status IN ('approved', 'rejected', 'failed')),
    downstream_action TEXT CHECK (downstream_action IS NULL OR downstream_action IN ('publish_link', 'publish_summary', 'edit_rewrite', 'reject_discard')),
    decision_reason TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    curated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE,
    CHECK (
        (curate_status = 'failed' AND downstream_action IS NULL) OR
        (curate_status = 'approved' AND downstream_action IN ('publish_link', 'publish_summary')) OR
        (curate_status = 'rejected' AND downstream_action IN ('edit_rewrite', 'reject_discard'))
    )
);

CREATE INDEX IF NOT EXISTS idx_curation_decision_source_item_id 
    ON curation_decision(source_item_id);

CREATE INDEX IF NOT EXISTS idx_curation_decision_status_action 
    ON curation_decision(curate_status, downstream_action);


-- 2. editor_brief table
CREATE TABLE IF NOT EXISTS editor_brief (
    editor_brief_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    brief_goal TEXT NOT NULL,
    target_format TEXT NOT NULL,
    key_claim TEXT,
    key_evidence TEXT,
    required_context TEXT,
    risk_flags TEXT, -- JSON Array formatted as string
    tone_guidance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_editor_brief_source_item_id 
    ON editor_brief(source_item_id);


-- 3. curation_output table
CREATE TABLE IF NOT EXISTS curation_output (
    curation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    display_title TEXT NOT NULL,
    summary_short TEXT NOT NULL,
    bullet_1 TEXT,
    bullet_2 TEXT,
    bullet_3 TEXT,
    source_attribution_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_curation_output_source_item_id 
    ON curation_output(source_item_id);
```

---

## 4. Pending Item Query

The `curate` queue selects items that have been successfully classified as `core` or `adjacent`, and do not yet have a decision OR have failed previously but have not exceeded 3 retry attempts.

```sql
SELECT 
    s.source_item_id, 
    s.title AS raw_title, 
    s.canonical_url,
    t.sanitized_text, 
    c.topic_class,
    c.classification_reason,
    c.governmental_involvement
FROM source_item s
JOIN source_item_text t ON s.source_item_id = t.source_item_id
JOIN classification_result c ON s.source_item_id = c.source_item_id
LEFT JOIN curation_decision r ON s.source_item_id = r.source_item_id
WHERE s.ingest_status = 'ingested'
  AND c.topic_class IN ('core', 'adjacent')
  AND (r.curation_decision_id IS NULL OR (r.curate_status = 'failed' AND r.retry_count < 3));
```

### Key Differences
* **Retry Safety:** Selecting previous `failed` items with `retry_count < 3` allows transient network or API rate limit errors to be retried automatically.
* **No Contract Leakage:** `c.additional_signals` is excluded from this query to strictly honor the policy that downstream modules must not rely on experimental schema columns.

---

## 5. Dependency on Upstream Schema Columns

The `curate` module's queue loader reads from the canonical SQLite database. To ensure decoupling, it assumes and depends only on the following specific schema contracts in the upstream tables:

### 5.1 `source_item` (Owned by `ingest`)
* **`source_item_id`**: `INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT` — Uniquely identifies each item. Used as the foreign key target.
* **`title`**: `TEXT NOT NULL` — The original raw title.
* **`canonical_url`**: `TEXT` (Nullable) — The URL to reference.
* **`ingest_status`**: `TEXT NOT NULL` — Evaluated as `'ingested'` to check suitability.

### 5.2 `source_item_text` (Owned by `ingest`)
* **`source_item_id`**: `INTEGER NOT NULL UNIQUE` — FK to `source_item(source_item_id)` with `ON DELETE CASCADE`. Guaranteed to have at most one row per source item.
* **`sanitized_text`**: `TEXT NOT NULL` — The cleaned content text used for LLM prompt context.

### 5.3 `classification_result` (Owned by `classify`)
* **`source_item_id`**: `INTEGER NOT NULL UNIQUE` — FK to `source_item(source_item_id)` with `ON DELETE CASCADE`. The `curate` queue loader only selects items where a classification result row already exists. Due to the `UNIQUE` key constraint, there is at most one classification result per source item (one-to-one relationship), avoiding duplicate processing entries.
* **`topic_class`**: `TEXT NOT NULL` — Evaluated as part of routing. Checked for inclusion in `('core', 'adjacent')`.
* **`governmental_involvement`**: `INTEGER` (Nullable) — Flag (`0` or `1`) passed to the curator model for additional context.

---

## 6. Curation Result Validation Matrix

To ensure data integrity, the system must enforce strict validation constraints between the resolved `downstream_action` and the generated metadata in the database rows. 

| Downstream Action | `curation_decision` Columns | `editor_brief` Status | `curation_output` Status | Bullet Points (`bullet_1`, `2`, `3`) |
| :--- | :--- | :--- | :--- | :--- |
| **`publish_link`** | `curate_status = 'approved'` | **MUST exist** | **MUST exist** | **MUST be NULL** |
| **`publish_summary`** | `curate_status = 'approved'` | **MUST exist** | **MUST exist** | **MUST all be NOT NULL** |
| **`edit_rewrite`** | `curate_status = 'rejected'` | **MUST exist** | **MUST NOT exist** | N/A (no curation output) |
| **`reject_discard`** | `curate_status = 'rejected'` | **MUST NOT exist** | **MUST NOT exist** | N/A (no curation output) |
| **`failed`** (Runner-side) | `curate_status = 'failed'`<br>`downstream_action = NULL` | **MUST NOT exist** | **MUST NOT exist** | N/A (no curation output) |

This validation matrix should be executed programmatically in:
1. The orchestrator's output schema parser before committing database writes.
2. The SQLite database schema validation constraints (where applicable) and database unit tests.
