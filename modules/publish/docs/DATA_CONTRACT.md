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

- If `author_metadata` is present, it must parse successfully as a JSON object.
- The parsed object must contain at least `source_module` and `writer_type`.
- If `author_metadata` is `NULL`, invalid JSON, not a JSON object, or missing required keys, the artifact fails validation and must not be exported.
- `publish` must not emit mixed output types for this field. The exported item JSON always uses the object form.

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
  },
  "source_fingerprint": "sha256:example"
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

The list must be sorted by `published_at DESC`, with a deterministic tiebreaker such as `slug ASC`.

### 6.3 Feed XML

Path:

```text
data/publish_export/<language_code>/feed.xml
```

The feed must be derived only from active published items in that language and must not retain withdrawn items.

### 6.4 Global Stats JSON

Path:

```text
data/publish_export/stats.json
```

This file should expose lightweight aggregate counts such as:

- total active published items by language
- total withdrawn language artifacts
- last export run timestamp

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

The older discussion has been preserved in `archive/docs/WHY_JSON.md`.
