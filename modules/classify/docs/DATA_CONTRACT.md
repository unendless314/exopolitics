# Classification Data Contract

**Document version:** v3.2  
**Updated:** 2026-06-13  
**Status:** Planning & Active rewrite draft

---

## 1. Purpose

`classification_result` stores the latest classification outcome for each `source_item` that has completed classification. The classify pending queue admits every ingested item with classifiable input: all `completed` items and low-context items whose `text_processing_reason` is not `post_cleanup_empty`. It excludes only `failed` items and `post_cleanup_empty` outcomes.

The design is constrained as follows:
* **One-to-One:** Each `source_item` has at most one classification record (`source_item_id` is unique).
* **Downstream Integration:** The **`curate`** module is the sole direct reader of this table. Downstream modules (like `publish` and `site`) must **not** read this table directly; they consume only curated and approved records.
* **MVP Version Simplification:** To simplify the database design for the MVP curation queue, we only retain the active/latest classification state per item. If reclassification happens, the existing row is updated or replaced. This is a deliberate MVP trade-off; audit trails of classification drift (due to prompt or model changes) are deferred for simplicity.
* **Additional Signals Governance:** The `additional_signals` JSON column is strictly reserved for allowlisted experimental metadata (e.g., `content_timeliness` or `primary_evidence_type`). Downstream consumer modules must not query or depend on any key within `additional_signals` for stable, production workflows. Promoting any experimental key to a stable contract requires a formal schema migration to a dedicated, typed database column.

---

## 2. Database Schema

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `classification_result_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate key. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | Foreign key referencing `source_item(source_item_id) ON DELETE CASCADE`. |
| `topic_class` | `TEXT` | `NOT NULL` | One of `'core'`, `'adjacent'`, `'irrelevant'`, `'unknown'`. |
| `classification_reason` | `TEXT` | `NULL` | Concise human-readable explanation behind the classification choice. |
| `classification_confidence` | `REAL` | `NULL` | Confidence score (`0.0` to `1.0`). |
| `content_density` | `TEXT` | `NULL` | Information density: `'low'`, `'medium'`, `'high'`. |
| `source_text_quality` | `TEXT` | `NULL` | Coherence and readability: `'poor'`, `'usable'`, `'strong'`. |
| `primary_language_code` | `TEXT` | `NULL` | Detected primary language (e.g., `'en'`, `'es'`). |
| `governmental_involvement` | `INTEGER` | `NULL` | Flag (`0` or `1`) indicating government or military involvement. |
| `additional_signals` | `TEXT` | `NULL` | JSON string containing experimental, non-contract signals. |
| `model_name` | `TEXT` | `NOT NULL` | LLM model name. |
| `prompt_version` | `TEXT` | `NOT NULL` | Prompt template version. |
| `classified_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |

---

## 3. SQLite DDL

This migration script will reside in `modules/classify/src/migrations/v001_initial_classify_tables.sql`:

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS classification_result (
    classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    topic_class TEXT NOT NULL CHECK (topic_class IN ('core', 'adjacent', 'irrelevant', 'unknown')),
    classification_reason TEXT,
    classification_confidence REAL CHECK (classification_confidence IS NULL OR (classification_confidence >= 0.0 AND classification_confidence <= 1.0)),
    content_density TEXT CHECK (content_density IS NULL OR content_density IN ('low', 'medium', 'high')),
    source_text_quality TEXT CHECK (source_text_quality IS NULL OR source_text_quality IN ('poor', 'usable', 'strong')),
    primary_language_code TEXT,
    governmental_involvement INTEGER CHECK (governmental_involvement IS NULL OR governmental_involvement IN (0, 1)),
    additional_signals TEXT,
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_classification_result_topic_class
    ON classification_result(topic_class);

CREATE INDEX IF NOT EXISTS idx_classification_result_source_item_id
    ON classification_result(source_item_id);
```

---

## 4. Pending Item Query

The `classify` queue identifies items that have been ingested and sanitized, but lack classification. 

```sql
SELECT 
    s.source_item_id, 
    s.title, 
    t.sanitized_text
FROM source_item s
JOIN source_item_text t ON s.source_item_id = t.source_item_id
LEFT JOIN classification_result c ON s.source_item_id = c.source_item_id
WHERE s.ingest_status = 'ingested'
  AND t.text_processing_status != 'failed'
  AND (
      t.text_processing_reason IS NULL
      OR t.text_processing_reason != 'post_cleanup_empty'
  )
  AND c.classification_result_id IS NULL;
```

### Benefits of the New Join
* **Guaranteed Sanitization:** By performing an `INNER JOIN` on `source_item_text`, we ensure we never attempt to classify items that have not yet had their text sanitized.
* **Separation of Concerns:** We filter on `text_processing_status` and `text_processing_reason` using the `source_item_text` table directly, allowing the database query to exclude only failed items and post-cleanup-empty outcomes from entering the classify queue. All other items, including low-context ones, are admitted. The explicit `IS NULL` branch preserves `completed` items, whose reason is normally `NULL`.
