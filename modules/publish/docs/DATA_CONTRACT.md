# Publish Data Contract

**Document version:** v2.0  
**Updated:** 2026-06-24  
**Status:** Active rewrite draft

---

## 1. Purpose

The `publish` module defines a downstream projection over approved, translated content so the repository can:

- preserve a stable slug for each publicly exposed source item
- track per-language publish synchronization state without re-owning editorial approval
- emit static public files that the `site` module can consume directly
- rebuild exports deterministically from canonical database state

The publish layer must remain a projection. `curation_decision.curate_status` is the source of truth for public eligibility; `publish_record` and `publish_language_status` must never become the business authority for approval or withdrawal.

---

## 2. Database Schema

### 2.1 `publish_record`

Stores the publish-layer identity of a source item and its permanently frozen slug.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `publish_record_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate primary key. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | FK to `source_item(source_item_id) ON DELETE CASCADE`. One publish record per source item. |
| `slug` | `TEXT` | `NOT NULL UNIQUE` | Stable route key shared across all exported languages for the same item. Once created, this value is treated as frozen. |
| `first_published_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp of the first successful publish-layer export for the item in any language. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 system timestamp for row creation. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 system timestamp for the last publish-layer mutation on the row. |

### 2.2 `publish_language_status`

Tracks language-specific export state as a downstream synchronization record.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `publish_language_status_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate primary key. |
| `publish_record_id` | `INTEGER` | `NOT NULL` | FK to `publish_record(publish_record_id) ON DELETE CASCADE`. |
| `language_code` | `TEXT` | `NOT NULL` | Exported language code. The contract does not hardcode a permanent fixed language set. |
| `publish_status` | `TEXT` | `NOT NULL` | Publish-layer state: `'published'` or `'withdrawn'`. |
| `published_at` | `TEXT` | `NULL` | UTC ISO-8601 timestamp of the most recent successful publish for this language. Preserved when status later becomes `withdrawn`. |
| `withdrawn_at` | `TEXT` | `NULL` | UTC ISO-8601 timestamp of the most recent downstream withdrawal synchronization, if the previously exported language artifact was removed. |
| `source_fingerprint` | `TEXT` | `NOT NULL` | Snapshot of `approved_content_record.content_fingerprint` used for the exported artifact version. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 system timestamp. |

### 2.3 Logical Constraints

- `publish_record` exists at most once per `source_item_id`.
- `publish_language_status` exists at most once per `(publish_record_id, language_code)`.
- `publish_status = 'withdrawn'` represents downstream file removal state only; it does not mean the item is deleted from canonical storage.
- `source_fingerprint` lets `publish` detect whether a previously exported language artifact corresponds to the current mother-draft version.
- slug generation occurs only when a `publish_record` row does not yet exist for the `source_item_id`.
- `publish_language_status` deliberately omits an `updated_at` column by design to keep the schema simple; modification events are traced through `published_at` and `withdrawn_at` fields depending on the active state.

---

## 3. SQLite DDL

This migration should reside in `modules/publish/src/migrations/v001_initial_publish_tables.sql` for the active rewrite.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS publish_record (
    publish_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    first_published_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_publish_record_source_item_id
    ON publish_record(source_item_id);

CREATE INDEX IF NOT EXISTS idx_publish_record_slug
    ON publish_record(slug);

CREATE TABLE IF NOT EXISTS publish_language_status (
    publish_language_status_id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_record_id INTEGER NOT NULL,
    language_code TEXT NOT NULL,
    publish_status TEXT NOT NULL CHECK (publish_status IN ('published', 'withdrawn')),
    published_at TEXT,
    withdrawn_at TEXT,
    source_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (publish_record_id) REFERENCES publish_record (publish_record_id) ON DELETE CASCADE,
    UNIQUE (publish_record_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_publish_language_status_record_lang
    ON publish_language_status(publish_record_id, language_code);

CREATE INDEX IF NOT EXISTS idx_publish_language_status_state
    ON publish_language_status(language_code, publish_status);
```

---

## 4. Upstream Read Dependencies

The `publish` module depends on these upstream contracts only:

### 4.1 `approved_content_record` (Shared Handoff Artifact)

- `parent_content_id`
- `source_item_id`
- `content_fingerprint`
- `approved_at`
- `author_metadata`

### 4.2 `translation_output` (Owned by `translate`)

- `parent_content_id`
- `source_item_id`
- `language_code`
- `display_title`
- `content`
- `source_fingerprint`
- `translation_status`
- `translated_at`

### 4.3 `curation_decision` (Owned by `curate`)

- `source_item_id`
- `curate_status`
- `downstream_action`

### 4.4 `source_item` (Owned by `ingest`)

- `source_item_id`
- `canonical_url`
- `published_at`

`publish` must not depend on optional or experimental upstream columns that are not explicitly locked in module docs.

---

## 5. Selection Query Shape

### 5.1 Exportable Language Rows

The runner needs a query shape equivalent to the following:

```sql
SELECT
    a.parent_content_id,
    a.source_item_id,
    a.content_fingerprint,
    a.approved_at,
    a.author_metadata,
    t.language_code,
    t.display_title,
    t.content,
    t.source_fingerprint,
    t.translated_at,
    c.curate_status,
    c.downstream_action,
    s.canonical_url,
    s.published_at AS source_published_at,
    pr.publish_record_id,
    pr.slug,
    pls.publish_language_status_id,
    pls.publish_status,
    pls.source_fingerprint AS published_source_fingerprint
FROM approved_content_record a
JOIN curation_decision c
    ON c.source_item_id = a.source_item_id
JOIN translation_output t
    ON t.parent_content_id = a.parent_content_id
   AND t.source_fingerprint = a.content_fingerprint
JOIN source_item s
    ON s.source_item_id = a.source_item_id
LEFT JOIN publish_record pr
    ON pr.source_item_id = a.source_item_id
LEFT JOIN publish_language_status pls
    ON pls.publish_record_id = pr.publish_record_id
   AND pls.language_code = t.language_code
WHERE c.curate_status = 'approved'
  AND t.translation_status = 'completed';
```

### 5.2 Strict-Match Article Eligibility

Under `strict_match`, the runner must publish a `source_item_id` only if every configured required language is present in the completed translation set for the current `content_fingerprint`.

The exact SQL may vary, but the behavior must be equivalent to:

- group rows by `source_item_id`
- verify all required public languages are present and completed
- exclude rows where the translation row still points to an older fingerprint

---

## 6. Export File Contracts

All public artifacts are emitted under `data/publish_export/`.

### 6.1 Item JSON

Path:

```text
data/publish_export/<language_code>/items/<slug>.json
```

**JSON Object Parsing Rule**:
The `publish` runner reads `approved_content_record.author_metadata` as a serialized JSON string from the database (TEXT). To provide a clean developer experience for the frontend (`site` module), the runner must deserialize this string and embed it as a structured nested object in the exported JSON file.

Contract requirements:

- `author_metadata` is required for every exportable artifact and must parse successfully as a JSON object.
- The parsed object must contain at least `source_module` and `writer_type`.
- If `author_metadata` is `NULL` in the database, invalid JSON, not a JSON object, or missing required keys, the artifact fails validation and must not be exported.
- `publish` must not emit mixed output types for this field. The exported item JSON always uses the object form.
- **Disclosure Note Generation**: The `disclosure_note` text is determined directly from `writer_type` without heuristic guessing:
  - If `writer_type` is `'human'` or `'hybrid'`, the note must be: `"This item is AI-assisted and human-curated."`
  - If `writer_type` is `'AI'` or `'machine'`, the note must be: `"This item is AI-generated."`
  - **Validation Rule**: To ensure reliability, when `writer_type` is `'human'` or `'hybrid'`, the `author_metadata` must contain a non-empty `editor` field. If `editor` is missing or empty for human/hybrid content, the artifact fails validation and must not be published (see [EXECUTION_POLICY.md](file:///C:/Users/user/Documents/derived-work/modules/publish/docs/EXECUTION_POLICY.md)).

Contract example:

```json
{
  "source_item_id": 3,
  "language_code": "en",
  "slug": "al-seckel-appears-in-epstein-related-files-and-correspondence",
  "display_title": "Al Seckel appears in Epstein-related files and correspondence",
  "content": "## Summary\n\nTranslated markdown content...",
  "canonical_url": "https://example.com/al-seckel-epstein-files",
  "source_published_at": "2026-06-16T08:00:00Z",
  "approved_at": "2026-06-16T12:00:00Z",
  "published_at": "2026-06-24T10:00:00Z",
  "downstream_action": "publish_summary",
  "disclosure_note": "This item is AI-assisted and human-curated.",
  "author_metadata": {
    "source_module": "edit",
    "writer_type": "human",
    "editor": "john_doe"
  }
}
```

### 6.2 Language Index JSON

Path:

```text
data/publish_export/<language_code>/index.json
```

Contract example:

```json
[
  {
    "slug": "al-seckel-appears-in-epstein-related-files-and-correspondence",
    "display_title": "Al Seckel appears in Epstein-related files and correspondence",
    "summary_short": "Translated first paragraph or derived preview text.",
    "canonical_url": "https://example.com/al-seckel-epstein-files",
    "source_published_at": "2026-06-16T08:00:00Z",
    "approved_at": "2026-06-16T12:00:00Z",
    "published_at": "2026-06-24T10:00:00Z"
  }
]
```

**Summary Short Parsing Rule**:
- `summary_short` is a short preview text. Since the system's translated content is already a highly condensed summary, this field is derived from the first paragraph (or a configured character limit) of the translated `content` body during publish compilation.

**Sorting Rule**:
- The list must be sorted by `source_published_at DESC`, with a deterministic tiebreaker `slug ASC`.
- The `published_at` timestamp is preserved strictly for publisher audit purposes (recording when the runner emitted the entry) and must not be used for sharding or sorting.

**Overlap & Limit Policy**:
- This index file is limited to the latest $N$ items (configured via `index_policy.latest_limit`).
- Because historical archives contain all items for a given month, items in recent months may exist in both `index.json` and the corresponding monthly archive. This overlap is intended by design and does not represent a duplicate data error.

### 6.3 Monthly Archive JSON

Path:

```text
data/publish_export/<language_code>/archives/archive_YYYY_MM.json
```

- Each file contains all items published within a specific calendar month, mapped strictly by their `source_published_at` (derived from `source_item.published_at`). Other fields like `approved_at` or `published_at` must not be used for classification to prevent month drifting.
- The structure of the JSON array is identical to `index.json` (see Section 6.2).
- Items inside the archive must be sorted by `source_published_at DESC` with a deterministic tiebreaker `slug ASC`.
- Historical archives are append-stable (immutable for normal incremental runs) but may be rewritten for compliance-driven withdrawal or correction synchronization. If a monthly archive file becomes empty after withdrawal, it must be deleted from disk and its entry must be removed from the archives index manifest.

### 6.4 Monthly Archive Index JSON (Manifest)

Path:

```text
data/publish_export/<language_code>/archives/index.json
```

Provides a manifest of available archives so downstream consumers (e.g. `site` module) can discover available monthly packages without scanning directory contents.

Contract example:

```json
[
  {
    "archive_month": "2026-06",
    "file_name": "archive_2026_06.json",
    "item_count": 89,
    "updated_at": "2026-06-25T02:00:00Z"
  },
  {
    "archive_month": "2026-05",
    "file_name": "archive_2026_05.json",
    "item_count": 142,
    "updated_at": "2026-06-24T10:00:00Z"
  }
]
```

- The list must be sorted by `archive_month DESC`.
- `updated_at` tracks the UTC ISO-8601 timestamp of the most recent write to that specific monthly archive file.

### 6.5 Global Stats JSON

Path:

```text
data/publish_export/stats.json
```

This file exposes lightweight aggregate counts and operational observation metrics:

- `total_active_published_items_by_language`: dictionary mapping language codes to total count of active published items
- `total_withdrawn_items_by_language`: dictionary mapping language codes to total count of withdrawn items
- `latest_index_count_by_language`: dictionary mapping language codes to count of items in their `index.json`
- `archive_month_count_by_language`: dictionary mapping language codes to count of historical monthly archive files
- `oldest_archive_month_by_language`: dictionary mapping language codes to their earliest archive month string (e.g., `"2026-05"`)
- `last_export_run_timestamp`: UTC ISO-8601 timestamp of the last export execution

---

## 7. Slug Policy

- The slug source should be the English translated title when available under the active coverage policy.
- Slugs must be lowercase and URL-safe.
- Duplicate slug collisions must be resolved deterministically, for example by suffixing `-2`, `-3`, and so on.
- Once persisted in `publish_record.slug`, the slug is frozen and must be reused even if the translated title later changes.

---

## 8. JSON Over Direct DB Consumption

The active architecture intentionally exports JSON artifacts instead of allowing the `site` module to read canonical operational tables directly.

Reasons:

- static hosting can consume exported files without private database access
- the site remains insulated from canonical schema churn
- the same publish artifacts can later feed other downstream consumers

---

## 9. Module Configuration

The `publish` module requires a configuration file following this schema. The default configuration file path is `modules/publish/config/publish_settings.yaml`, but the system must allow specifying a custom configuration file path during runtime execution. This configuration defines the active language set, publication policies, and batch constraints.

### 9.1 Schema Specification

```yaml
# Target languages that are configured for public display.
# Every code listed here must exist in translation_output.language_code.
target_languages:
  zh: "Traditional Chinese"
  en: "English"

# Coverage policy for publication eligibility.
# - strict_match: require completed translation for all target_languages before exporting.
coverage_policy: "strict_match"

execution_policy:
  # Default path for static export files
  default_export_dir: "data/publish_export"
  # Batch size for chunked database queries and file writes
  batch_size: 1000

# Index and archive generation policy
index_policy:
  # Maximum items retained in the latest index.json file
  latest_limit: 1000
  # Granularity for partitioning historical content (only 'month' is supported)
  archive_granularity: "month"
```

### 9.2 Validation Rules

- `target_languages` must contain a non-empty dictionary of language mappings.
- `coverage_policy` must be a supported string matching active strategies (currently `'strict_match'`).
- `execution_policy.batch_size` must be a positive integer greater than zero.
- `index_policy.latest_limit` must be a positive integer greater than zero.
- `index_policy.archive_granularity` must equal `"month"`.

If configuration validation fails due to structural or schema errors (such as missing required keys, negative bounds, or invalid data types), the runner must abort immediately. Warning-level runtime validation rules (such as missing database records for a configured target language during cold start) are handled per the rules defined in [EXECUTION_POLICY.md](./EXECUTION_POLICY.md) to allow graceful warning output and bypass.

