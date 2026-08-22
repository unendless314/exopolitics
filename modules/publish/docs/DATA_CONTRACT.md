# Publish Data Contract

**Document version:** v3.4
**Updated:** 2026-08-22
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

### 2.3 `publish_archive_metadata`

Stores publish-owned logical write timestamps for monthly archive files. The
archives index manifest (`archives/index.json`) reads `updated_at` from this
table so the value records the logical write time of the archive's most recent
content change, rather than an aggregate over item-level publish timestamps.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `language_code` | `TEXT` | `NOT NULL` | Exported language code. Part of the composite primary key `(language_code, archive_month)`. |
| `archive_month` | `TEXT` | `NOT NULL` | Calendar month key in `YYYY-MM` form, derived strictly from `source_item.published_at`. Part of the composite primary key. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 logical clock of the run whose generation build last changed the archive file's content (creation, withdrawal- or correction-driven change, or full rebuild restamp). |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 system timestamp for row creation. |

Lifecycle rules:

- rows are synced in a short transaction after each generation is fully built and before the pointer switch: every planned stamp is upserted and rows whose month is no longer active are deleted
- the recorded `updated_at` value advances only when the archive's content changes. The stamping decision for each active month is, by priority:
  1. a `rebuild` run stamps every active month with the run's logical clock
  2. a missing metadata row (databases created before this table existed) is healed with the run's logical clock
  3. if the live generation's hash stream (`file_hashes.jsonl`, see Section 6.7) records a digest for the archive file that matches the planned digest, the recorded DB value is kept verbatim
  4. if the stream has no record for the path (including a legacy pre-stream live generation, which carries no reuse information), the planned digest is compared against the fallback root file's digest (the live generation root, set only when a pointer exists); an equal digest — i.e. equal bytes — keeps the recorded DB value
  5. anything else is stamped with the run's logical clock
- a run whose content did not change builds nothing and leaves every row (and therefore every manifest `updated_at`) unchanged
- healing a missing row changes the planned manifest, so it builds exactly one new generation carrying the new stamp; the archive file bytes themselves are unchanged
- when an archive month becomes inactive (e.g. emptied by withdrawal), its row is deleted during the metadata sync of that build and the new generation simply has no file for it; a later recreation of the same month starts a new row with a new logical write timestamp
- when a language leaves the configured set, its rows are deleted unconditionally during reconciliation; the next generation contains no artifacts for that language
- a full rebuild restamps every active month with the rebuild run's logical clock; rows for months with no active items are removed

### 2.4 Logical Constraints

- `publish_record` exists at most once per `source_item_id`.
- `publish_language_status` exists at most once per `(publish_record_id, language_code)`.
- `publish_status = 'withdrawn'` represents downstream file removal state only; it does not mean the item is deleted from canonical storage.
- `source_fingerprint` lets `publish` detect whether a previously exported language artifact corresponds to the current mother-draft version.
- slug generation occurs only when a `publish_record` row does not yet exist for the `source_item_id`.
- `publish_language_status` deliberately omits an `updated_at` column by design to keep the schema simple; modification events are traced through `published_at` and `withdrawn_at` fields depending on the active state.

---

## 3. SQLite DDL

The initial publish tables reside in `modules/publish/src/migrations/v001_initial_publish_tables.sql`; the archive metadata table resides in `modules/publish/src/migrations/v002_archive_metadata.sql`.

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

`v002_archive_metadata.sql`:

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS publish_archive_metadata (
    language_code TEXT NOT NULL,
    archive_month TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (language_code, archive_month)
);
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
- `summary_short`
- `bullet_1`
- `bullet_2`
- `bullet_3`
- `source_fingerprint`
- `translation_status`
- `translated_at`

The legacy monolithic `content` column has been removed upstream; `publish` reads the five structured content fields above instead.

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
    t.summary_short,
    t.bullet_1,
    t.bullet_2,
    t.bullet_3,
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

All public artifacts are emitted under `data/publish_export/` as immutable, complete export generations behind an atomic pointer:

```text
data/publish_export/
  current.json                      # atomic pointer (commit point); the only reader entry point
  generations/<generation-id>/
    stats.json
    meta.json
    file_hashes.jsonl
    <language_code>/
      index.json
      items/<slug>.json
      archives/index.json
      archives/archive_YYYY_MM.json
```

- A generation id is the building run's logical UTC timestamp with colons replaced by hyphens (`YYYY-MM-DDTHH-MM-SSZ`); same-second collisions get a `-r2`/`-r3` suffix. Ids always match `^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$`.
- Every generation is a complete snapshot of the active published set and is immutable once published. Readers enter exclusively through `current.json`, resolve the referenced generation directory, and read inside it; the pointer switch is the single commit point, so readers see either the complete old generation or the complete new one, never partial output.
- Physically, unchanged artifacts of a new generation may be hardlinks sharing an inode with the trusted preceding generation (a storage optimization — see [EXECUTION_POLICY.md](./EXECUTION_POLICY.md) Section 6.1). Logically every generation remains a complete independent snapshot; this is why in-place mutation of any generation file is forbidden (see Section 6.7).
- A generation is complete even when empty: every configured language always has an `index.json` (`[]`), an archives manifest `archives/index.json` (`[]`), and explicit `items/` and `archives/` directories.
- Artifact bytes are serialized as `json.dumps(obj, indent=2, ensure_ascii=False)` UTF-8 with no trailing newline — unchanged from the pre-generation runner.

### 6.1 Item JSON

Path:

```text
data/publish_export/generations/<generation-id>/<language_code>/items/<slug>.json
```

**JSON Object Parsing Rule**:
The `publish` runner reads `approved_content_record.author_metadata` as a serialized JSON string from the database (TEXT). To provide a clean developer experience for the frontend (`site` module), the runner must deserialize this string and embed it as a structured nested object in the exported JSON file.

Contract requirements:

- `author_metadata` is required for every exportable artifact and must parse successfully as a JSON object.
- The parsed object must contain at least `source_module` and `writer_type`.
- `source_module` must be a JSON string that remains non-empty after trimming whitespace; a missing key, a non-string JSON value, an empty string, or a whitespace-only string all fail validation. No type coercion is applied.
- If `author_metadata` is `NULL` in the database, invalid JSON, not a JSON object, or missing required keys, the artifact fails validation and must not be exported.
- `publish` must not emit mixed output types for this field. The exported item JSON always uses the object form.
- **Disclosure Note Generation**: The `disclosure_note` text is determined directly from `writer_type` without heuristic guessing:
  - If `writer_type` is `'human'` or `'hybrid'`, the note must be: `"This item is AI-assisted and human-curated."`
  - If `writer_type` is `'AI'` or `'machine'`, the note must be: `"This item is AI-generated."`
  - **Validation Rule**: To ensure reliability, when `writer_type` is `'human'` or `'hybrid'`, the `author_metadata` must contain an `editor` field that is a JSON string remaining non-empty after trimming whitespace, under the same type rule as `source_module` (no coercion of numbers or other JSON types). If `editor` is missing, not a string, empty, or whitespace-only for human/hybrid content, the artifact fails validation and must not be published (see [EXECUTION_POLICY.md](./EXECUTION_POLICY.md)).

**Structured Content Fields**:
- `summary_short` is required for every exported item and must be a string that remains non-empty after trimming whitespace. It is passed through from `translation_output.summary_short` and serves as the single summary source for item, index, and archive entries.
- `bullets` is required on every item JSON and must never be omitted. It has exactly two valid shapes:
  - for `downstream_action = 'publish_summary'`: an object containing exactly the keys `key_claim`, `evidence_level`, and `objective_impact`, each with a non-empty string value
  - for `downstream_action = 'publish_link'`: JSON `null`
- An empty object or a partial key set is never a valid `bullets` value.
- The semantic key mapping is established exactly once, inside `publish`; no other module assigns these keys:

| Upstream `translation_output` Field | Export `bullets` Key |
| :--- | :--- |
| `bullet_1` | `key_claim` |
| `bullet_2` | `evidence_level` |
| `bullet_3` | `objective_impact` |

- Exported content values must not carry a presentation UI label prefix; those labels are applied by `site` at build time. The forbidden prefixes are the three English labels ("Key Claim", "Evidence Level", "Objective Impact") plus every zh/ja variant observed in the field — zh: 主要主張, 關鍵主張, 核心主張, 證據層級, 證據等級, 客觀影響, 實際影響; ja: 主要な主張, 主張の要点, 証拠の水準, 証拠レベル, 証拠水準, エビデンスレベル, 客観的な影響, 客観的影響, 目的上の影響 (see known_issues/resolved/TRANSLATION_LABEL_LEAKAGE.md section 4.2; the item payload schema fixture encodes the same list).

Contract example:

```json
{
  "source_item_id": 3,
  "language_code": "en",
  "slug": "al-seckel-appears-in-epstein-related-files-and-correspondence",
  "display_title": "Al Seckel appears in Epstein-related files and correspondence",
  "summary_short": "Translated short summary text.",
  "bullets": {
    "key_claim": "Key claim text.",
    "evidence_level": "Evidence level text.",
    "objective_impact": "Objective impact text."
  },
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
data/publish_export/generations/<generation-id>/<language_code>/index.json
```

Contract example:

```json
[
  {
    "slug": "al-seckel-appears-in-epstein-related-files-and-correspondence",
    "display_title": "Al Seckel appears in Epstein-related files and correspondence",
    "summary_short": "Translated short summary text.",
    "canonical_url": "https://example.com/al-seckel-epstein-files",
    "source_published_at": "2026-06-16T08:00:00Z",
    "approved_at": "2026-06-16T12:00:00Z",
    "published_at": "2026-06-24T10:00:00Z"
  }
]
```

**Summary Short Rule**:
- `summary_short` is read directly from `translation_output.summary_short` for the item's language. It must not be derived from any larger body field; the previous `extract_summary_short()` derivation and every body-derived summary fallback are removed.

**Sorting Rule**:
- The list must be sorted by `source_published_at DESC`, with a deterministic tiebreaker `slug ASC`.
- The `published_at` timestamp is preserved strictly for publisher audit purposes (recording when the runner emitted the entry) and must not be used for sharding or sorting.

**Overlap & Limit Policy**:
- This index file is limited to the latest $N$ items (configured via `index_policy.latest_limit`).
- Because historical archives contain all items for a given month, items in recent months may exist in both `index.json` and the corresponding monthly archive. This overlap is intended by design and does not represent a duplicate data error.

### 6.3 Monthly Archive JSON

Path:

```text
data/publish_export/generations/<generation-id>/<language_code>/archives/archive_YYYY_MM.json
```

- Each file contains all items published within a specific calendar month, mapped strictly by their `source_published_at` (derived from `source_item.published_at`). Other fields like `approved_at` or `published_at` must not be used for classification to prevent month drifting.
- The structure of the JSON array is identical to `index.json` (see Section 6.2).
- Items inside the archive must be sorted by `source_published_at DESC` with a deterministic tiebreaker `slug ASC`.
- Historical archives are append-stable: a published generation is never modified in place. Withdrawal- or correction-driven changes take effect in the next generation, whose archive file for that month reflects the post-sync set. If a monthly archive becomes empty after withdrawal, the next generation has no file for that month and the archives index manifest has no entry for it.

### 6.4 Monthly Archive Index JSON (Manifest)

Path:

```text
data/publish_export/generations/<generation-id>/<language_code>/archives/index.json
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
- The manifest is always written for every configured language, empty as `[]` when the language has no monthly archives.
- `updated_at` records the logical write time of the archive's most recent content change. It is read from the publish-owned `publish_archive_metadata` state (see Section 2.3), not derived from item-level publish timestamps and not from file-system mtime. Active months that predate `publish_archive_metadata` are restamped once with the run's logical clock (heal), which changes the planned manifest and therefore builds exactly one new generation while leaving the archive bytes unchanged (see Section 2.3).

### 6.5 Global Stats JSON

Path:

```text
data/publish_export/generations/<generation-id>/stats.json
```

This file exposes lightweight aggregate counts and operational observation metrics:

- `total_active_published_items_by_language`: dictionary mapping language codes to total count of active published items
- `total_withdrawn_items_by_language`: dictionary mapping language codes to total count of withdrawn items
- `latest_index_count_by_language`: dictionary mapping language codes to count of items in their `index.json`
- `archive_month_count_by_language`: dictionary mapping language codes to count of historical monthly archive files
- `oldest_archive_month_by_language`: dictionary mapping language codes to their earliest archive month string (e.g., `"2026-05"`)
- `last_export_run_timestamp`: UTC ISO-8601 timestamp frozen at the generation's build time. It is artifact content: a no-change run builds nothing, so this value does not advance. The run freshness signal lives on the pointer's `last_successful_run_at` (see Section 6.6).

### 6.6 Generation Pointer (`current.json`)

Path:

```text
data/publish_export/current.json
```

The pointer is the commit point of every run and the only entry point for readers. It is switched atomically (sibling temp file plus same-volume `os.replace`; a sharing violation is retried a limited number of times, then the run fails stop with the old pointer still valid). A no-change successful run refreshes only `last_successful_run_at`, atomically, without building a generation.

Contract example:

```json
{
  "generation": "2026-08-17T12-30-45Z",
  "export_completed_at": "2026-08-17T12:30:45Z",
  "last_successful_run_at": "2026-08-17T18:05:11Z",
  "languages": ["zh", "en"],
  "content_fingerprint": "sha256-exportstate-v1:9f2c1d4e..."
}
```

- `generation`: id of the live generation directory under `generations/`; must match the strict generation id format (see Section 6) and reference an existing directory.
- `export_completed_at`: UTC ISO-8601 logical timestamp of the run that built the live generation.
- `last_successful_run_at`: UTC ISO-8601 logical timestamp of the most recent successful run; the pipeline freshness signal.
- `languages`: the configured language set of the live generation; always a non-empty list of language codes. The pointer is the authoritative language set for downstream consumers, so an empty list is invalid even though every generation carries per-language artifacts.
- `content_fingerprint`: versioned digest of the planned export state, in the form `sha256-exportstate-v1:<64 lowercase hex>`. It is recorded verbatim so a future algorithm upgrade is an explicit rebuild trigger. Consumers must treat it as an opaque string.

A corrupt pointer (unparseable JSON, missing fields, malformed generation id, a calendar-impossible timestamp such as `2026-02-30T12:00:00Z`, an empty `languages` list, or a missing generation directory) is a fail-stop state: the runner raises instead of silently rebuilding, and readers must do the same.

### 6.7 Generation Metadata (`meta.json` + `file_hashes.jsonl`)

Paths:

```text
data/publish_export/generations/<generation-id>/meta.json
data/publish_export/generations/<generation-id>/file_hashes.jsonl
```

Per-generation metadata and content hash bookkeeping, written when the generation is built:

```json
{
  "generation": "2026-08-17T12-30-45Z",
  "created_at": "2026-08-17T12:30:45Z",
  "content_fingerprint": "sha256-exportstate-v1:9f2c1d4e...",
  "file_hashes": "file_hashes.jsonl"
}
```

- `meta.json` keeps the scalar fields (`generation`, `created_at`, `content_fingerprint`) plus the `"file_hashes": "file_hashes.jsonl"` reference. The reference value is always exactly `file_hashes.jsonl`; any other value — a JSON `null` included — is fail-stop. Both files are builder-owned metadata, not reusable export artifacts; they are written fresh for every new generation after the artifact digests are known.
- `file_hashes.jsonl` is a newline-delimited JSON stream with exactly one record per emitted artifact in fixed artifact order (per configured language in config order: `index.json`, `archives/index.json`, monthly archives month ASC, item payloads slug ASC; `stats.json` last), each record `{"path": "<generation-relative path>", "digest": "sha256:<hex>"}`. Digests are of the **actual written bytes** of every artifact, item payloads included. The runner uses the live generation's stream to decide archive `updated_at` stamping (see Section 2.3) and hardlink reuse (see [EXECUTION_POLICY.md](./EXECUTION_POLICY.md) Section 6.1) without re-reading artifact files. Both sides stay memory-bounded: the writer appends one record per artifact as it is linked or written, and the reader streams records line by line — no table proportional to item count is ever held in memory (see [EXECUTION_POLICY.md](./EXECUTION_POLICY.md) Section 9).
- **Dual-digest rule for `stats.json`:** `content_fingerprint` deliberately hashes stats.json's content excluding `last_export_run_timestamp` so run wall-clock never perturbs the build decision, while the stats.json stream record always records the digest of the real written bytes including that timestamp — so the recorded stream never disagrees with the disk. A corollary: because the stats timestamp advances with every build, stats.json bytes almost always differ between generations, so stats.json is physically written rather than linked in practice.
- **Stream integrity** is validated as the stream is read; all of the following are fail-stop corruption, never silent rebuild triggers: a missing or unparseable `meta.json`; a referenced stream that is missing or empty (every legitimately built generation records at least `stats.json` and the per-language aggregate files, so an empty stream is corruption, not a zero-data state); a malformed record; an illegal generation-relative path (leading `/`, backslash, drive-letter colon, or empty/`.`/`..` segments) or a repeated path; and a final record that is not `stats.json` (suffix-truncation detection — `stats.json` is always the last artifact in the fixed order, so even a valid-prefix truncation landing on a line boundary is caught). Full expected-sequence validation is deliberately out of scope: the expected artifact set is unknown when the stream is read, and a missing or digest-mismatched entry degrades safely to a physical write.
- **Legacy (pre-stream) transition:** a `meta.json` without the `file_hashes` reference is legacy **only** when it positively matches the legacy aggregate shape — strict generation id, calendar-valid `created_at`, well-formed `content_fingerprint`, and a non-empty `aggregate_file_hashes` object whose keys are legal generation-relative paths and whose values are all `sha256:<64 hex>` digests. Such a generation is tolerated as having **no reuse information**: the legacy table is read solely as a format witness and its hashes are never consulted for reuse; no-change runs against it neither fail nor spuriously build (archive stamping falls back to the digest-compare rule of Section 2.3), and the first content-changing build physically writes every artifact and establishes the full stream. Any other reference-less `meta.json` — for example a stream-era file whose reference field was lost — is corruption and fails stop.
- **Immutability is safety-critical:** generation contents — including `meta.json` and the hash stream — must never be overwritten, truncated, chmod-ed or replaced in place. Because unchanged artifacts are shared across generations as hardlinks to the same inode, an in-place edit would silently rewrite every generation sharing the inode. The repair path for any suspected corruption is `rebuild`, which physically rewrites everything and re-establishes verified bytes.

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


