# Classification Data Contract

**Document version:** v2.0  
**Updated:** 2026-06-03  
**Status:** Active

---

## 1. Purpose

`classification_result` stores the latest initial classification output for each `source_item`.

The table is intentionally narrow in the MVP:

* one row per `source_item`
* no historical classification versions
* no workflow-state column such as `classification_status`
* low-context items are represented through `topic_class = 'unknown'`

The repository may also contain draft SQL migration artifacts under `modules/classify/src/migrations/` that mirror this contract. Those files should be treated as implementation-preparation artifacts, not as evidence that runtime implementation is already complete.

---

## 2. Design Rules

* **Timestamps:** UTC ISO-8601 second-precision text: `YYYY-MM-DDTHH:MM:SSZ`
* **Primary key:** SQLite auto-increment integer
* **One-to-one rule:** `source_item_id` is unique in `classification_result`
* **Overwrite rule:** if future reclassification is introduced, the module updates the existing row instead of keeping history

---

## 3. Schema

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `classification_result_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate key. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | Foreign key to `source_item(source_item_id) ON DELETE CASCADE`. |
| `topic_class` | `TEXT` | `NOT NULL` | One of `core`, `adjacent`, `irrelevant`, `unknown`. |
| `classification_reason` | `TEXT` | `NULL` | Concise explanation for the classification outcome. |
| `classification_confidence` | `REAL` | `NULL` | Confidence score between `0.0` and `1.0`. May be `NULL` for deterministic low-context `unknown` results. |
| `edit_candidate` | `INTEGER` | `NOT NULL DEFAULT 0` | Boolean flag encoded as `0` or `1`. |
| `model_name` | `TEXT` | `NOT NULL` | LLM model identifier or deterministic classifier label. |
| `prompt_version` | `TEXT` | `NOT NULL` | Prompt template version or deterministic rule version. |
| `classified_at` | `TEXT` | `NOT NULL` | Time the classification decision was finalized. |
| `created_at` | `TEXT` | `NOT NULL` | Time the row was written. |

---

## 4. SQLite DDL

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS classification_result (
    classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    topic_class TEXT NOT NULL CHECK (topic_class IN ('core', 'adjacent', 'irrelevant', 'unknown')),
    classification_reason TEXT,
    classification_confidence REAL CHECK (classification_confidence >= 0.0 AND classification_confidence <= 1.0),
    edit_candidate INTEGER NOT NULL DEFAULT 0 CHECK (edit_candidate IN (0, 1)),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_classification_result_topic_class
    ON classification_result(topic_class);
```

---

## 5. Pending Item Query

```sql
SELECT s.source_item_id, s.title, s.summary, s.published_at, s.canonical_url
FROM source_item s
LEFT JOIN classification_result c ON s.source_item_id = c.source_item_id
WHERE s.ingest_status = 'ingested'
  AND c.classification_result_id IS NULL;
```

This query defines the MVP pending queue.

---

## 6. Persistence Semantics

### 6.1 Successful LLM classification

When the LLM returns a valid structured result, the module writes a `classification_result` row.

### 6.2 Deterministic low-context classification

When the combined feed `title + summary` length is below `min_context_characters`, the module skips the LLM call and writes:

* `topic_class = 'unknown'`
* `classification_confidence = NULL`
* `edit_candidate = 0`
* `classification_reason` must clearly indicate that the feed metadata is below the minimum context threshold and may include the measured length and configured threshold value
* `model_name = 'deterministic-low-context'`
* `prompt_version = 'rule_v1'`

### 6.3 Failed LLM execution

If the LLM call fails after all retries, the module does not write a row for that item. The item remains pending for a future run.

### 6.4 State boundary

`classify` does not modify `source_item.ingest_status`. The existence of a `classification_result` row is sufficient to indicate that an item has completed an initial classification pass.
