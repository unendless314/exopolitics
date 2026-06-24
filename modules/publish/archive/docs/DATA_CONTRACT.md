# Publish Data Contract

**Document version:** v1.0  
**Updated:** 2026-06-16  
**Status:** Active planning & design

---

## 1. Database Contract: `publish_record` & `publish_language_status`

To maintain an audit trail of published items, enforce global slug uniqueness per article, and track publication status per language, the `publish` module writes to two tables in `data/canonical.db`:

### 1.1 `publish_record` (Article Publication Registry)
Stores the global identity and uniform URL slug of a published article. Exactly one row exists per published `source_item_id`.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `publish_record_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate key. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | FK to `source_item(source_item_id) ON DELETE CASCADE`. Ensures one entry per article. |
| `slug` | `TEXT` | `NOT NULL UNIQUE` | URL-safe name shared across all languages. Enforced globally unique. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |

### 1.2 `publish_language_status` (Per-Language Publication Status)
Tracks the independent publication status, timestamp, and audit trail of each language for a registered article.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `publish_language_status_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate key. |
| `publish_record_id` | `INTEGER` | `NOT NULL` | FK to `publish_record(publish_record_id) ON DELETE CASCADE`. |
| `language_code` | `TEXT` | `NOT NULL` | Language code (e.g., `'zh'`, `'en'`, `'ja'`). |
| `publish_status` | `TEXT` | `NOT NULL` | Outcome state: `'published'` or `'withdrawn'`. |
| `published_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp when this language was published. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

### SQLite DDL
This migration script will reside in `modules/publish/src/migrations/v002_initial_publish_tables.sql`:

```sql
PRAGMA foreign_keys = ON;

-- Parent table: Article registry with unique slug
CREATE TABLE IF NOT EXISTS publish_record (
    publish_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_publish_record_slug ON publish_record(slug);

-- Child table: Per-language publishing state
CREATE TABLE IF NOT EXISTS publish_language_status (
    publish_language_status_id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_record_id INTEGER NOT NULL,
    language_code TEXT NOT NULL CHECK (language_code IN ('zh', 'en', 'ja')),
    publish_status TEXT NOT NULL CHECK (publish_status IN ('published', 'withdrawn')),
    published_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (publish_record_id) REFERENCES publish_record (publish_record_id) ON DELETE CASCADE,
    UNIQUE (publish_record_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_publish_language_status_state ON publish_language_status(language_code, publish_status);
```

---

## 2. Upstream Database Queries

The `publish` module reads from `source_item` (owned by `ingest`), `curation_decision` (owned by `curate`), `translation_output` (owned by `translate`), and the local publishing tables.

### 2.1 Fetching Strict-Match Multilingual Items for Export
For the MVP strict-match policy, `publish` exports an item only when every configured language is `completed` in `translation_output`. The query filters to complete multilingual groups, checking the publishing status for each language:

```sql
SELECT 
    s.source_item_id,
    s.canonical_url,
    s.published_at AS original_published_at,
    cd.downstream_action,
    tr.language_code,
    tr.display_title,
    tr.content AS translated_content,
    -- English title is fetched to generate slug if needed
    (SELECT display_title FROM translation_output 
     WHERE source_item_id = s.source_item_id AND language_code = 'en') AS en_title,
    pr.slug
FROM source_item s
JOIN curation_decision cd ON s.source_item_id = cd.source_item_id
JOIN translation_output tr ON s.source_item_id = tr.source_item_id
LEFT JOIN publish_record pr ON s.source_item_id = pr.source_item_id
LEFT JOIN publish_language_status pls ON pr.publish_record_id = pls.publish_record_id AND tr.language_code = pls.language_code
WHERE s.ingest_status = 'ingested'
  AND cd.curate_status = 'approved'
  AND tr.translation_status = 'completed'
  AND NOT EXISTS (
      SELECT 1
      FROM translation_output tr_required
      WHERE tr_required.source_item_id = s.source_item_id
        AND tr_required.language_code IN ('zh', 'en', 'ja')
        AND tr_required.translation_status <> 'completed'
  )
  AND (pls.publish_language_status_id IS NULL OR pls.publish_status = 'withdrawn');
```

---

## 3. Export File Specifications

Export files are compiled into language-specific folders under `data/publish_export/<language_code>/` during `run` or `rebuild`.

### 3.1 Individual Article JSON (`<language_code>/items/<slug>.json`)
Each item gets a dedicated JSON file for deep linking under its corresponding language folder.

```json
{
  "source_item_id": 3,
  "slug": "al-seckel-appears-in-epstein-related-files-and-correspondence",
  "display_title": "Al Seckel appears in Epstein-related files and correspondence",
  "canonical_url": "https://example.com/al-seckel-epstein-files",
  "downstream_action": "publish_summary",
  "content": "This article reviews references to Al Seckel in released Epstein-related files...\n\n* **Core Claim**: Claim detail...\n* **Evidence Level**: Evidence detail...\n* **Objective Implication**: Implication detail...",
  "disclosure_note": "This item is aggregated and summarized by AI, curated by human editors.",
  "original_published_at": "2026-06-16T08:00:00Z",
  "published_at": "2026-06-16T15:00:00Z"
}
```

### 3.2 List Index JSON (`<language_code>/index.json`)
A flat array of all active published items for a given language, sorted by `published_at DESC`. It contains only the fields needed to render a list card. The `summary_short` is extracted dynamically from the first paragraph of the markdown `content` (split by `\n\n`).

```json
[
  {
    "slug": "al-seckel-appears-in-epstein-related-files-and-correspondence",
    "display_title": "Al Seckel appears in Epstein-related files and correspondence",
    "summary_short": "This article reviews references to Al Seckel in released Epstein-related files...",
    "downstream_action": "publish_summary",
    "original_published_at": "2026-06-16T08:00:00Z",
    "published_at": "2026-06-16T15:00:00Z"
  }
]
```

### 3.3 Syndication RSS Feed (`<language_code>/feed.xml`)
Standard RSS 2.0 feed using the `display_title` and the extracted `summary_short` for items.
- RSS `<link>` maps to: `<domain>/<language_code>/items/<slug>`
- RSS `<guid>` is set to: the `<slug>` or `<domain>/<language_code>/items/<slug>`
