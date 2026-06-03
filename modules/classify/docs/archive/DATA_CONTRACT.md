# Classification Data Contract Specification

**Document version:** v1.0  
**Updated:** 2026-06-03  
**Status:** Concrete Specification

---

## 1. Column Types & Design Rules

To maintain compatibility with SQLite and ensure easy migration to PostgreSQL in the future:
* **Timestamps:** Standardized to **UTC ISO-8601 second-precision text: `YYYY-MM-DDTHH:MM:SSZ`** (exactly 20 characters).
* **Identifiers:** SQLite auto-increment integers will be used for surrogate primary keys (`classification_result_id`).
* **Statuses:** Stored as `TEXT` check-constraints to limit variables to predefined values.

---

## 2. Table Schema: `classification_result`

This table stores classification details for processed `source_item` rows. Most rows are produced by an LLM call, but low-context items may be classified directly by deterministic module rules.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `classification_result_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Primary key identifier. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | Foreign key referencing `source_item(source_item_id) ON DELETE CASCADE`. UNIQUE ensures a strict 1-to-1 relationship (maintaining only the latest result). |
| `topic_class` | `TEXT` | `NOT NULL` | The assigned category: `'core'`, `'adjacent'`, `'irrelevant'`, or `'unknown'`. |
| `classification_reason` | `TEXT` | `NULL` | Concise reason provided by the model. |
| `classification_confidence`| `REAL` | `NULL` | Model-provided confidence score (between `0.0` and `1.0`). |
| `edit_candidate` | `INTEGER` | `NOT NULL DEFAULT 0` | 0 (No) or 1 (Yes) indicating if this is suggested for edit/rewrite. |
| `model_name` | `TEXT` | `NOT NULL` | Name and version of the LLM used (e.g. `'gemini-1.5-flash'`). |
| `prompt_version` | `TEXT` | `NOT NULL` | Prompt template version identifier (e.g. `'v1.0'`). |
| `classified_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp representing when the LLM finished processing. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp representing database entry creation time. |

---

## 3. SQLite DDL

This DDL will be packaged as a migration file in `modules/classify/src/migrations/v002_initial_classify_tables.sql`.

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

CREATE INDEX IF NOT EXISTS idx_classification_result_topic_class ON classification_result(topic_class);
```

---

## 4. Query Boundary & State Logic

The classifier scans the canonical database for any `source_item` that has **not** been classified.

### 4.1 SQL to Fetch Pending Items
```sql
SELECT s.source_item_id, s.title, s.summary, s.published_at, s.canonical_url
FROM source_item s
LEFT JOIN classification_result c ON s.source_item_id = c.source_item_id
WHERE s.ingest_status = 'ingested'
  AND c.classification_result_id IS NULL;
```

### 4.2 State Update Rule
The classification process does **not** update the `ingest_status` of the `source_item` row, respecting module boundaries and ensuring immutability of the original ingest table. The presence of a row in `classification_result` serves as the implicit indicator that an item has completed an initial classification pass. Downstream review logic in the `review` module will join `source_item` with `classification_result` to populate its review queue.

### 4.3 Low-Context Rule
If the combined feed `title` and `summary` length is below the configured `min_context_characters` threshold, the module does not invoke the LLM for that item. Instead, it writes a deterministic classification result with these semantics:

* `topic_class = 'unknown'`
* `classification_reason` explains that the feed metadata lacks enough context for reliable classification
* `classification_confidence = NULL`
* `edit_candidate = 0`

This keeps low-quality feed entries visible to later enrichment or manual triage without mislabeling them as `irrelevant`.
