# Translate Data Contract

**Document version:** v1.3  
**Updated:** 2026-06-19  
**Status:** Locked Contract  

> [!IMPORTANT]
> **Co-location Disclaimer**: The `approved_content_record` table represents a shared canonical handoff capability (not owned solely by the `translate` module). For implementation simplicity, its schema migrations and assembly helper scripts are temporarily co-located under `modules/translate/`. This co-location does not alter the module boundaries defined in [MODULE_BOUNDARIES.md](file:///C:/Users/user/Documents/exopolitics/docs/MODULE_BOUNDARIES.md); `translate` remains a pure downstream consumer of this handoff table.

---

## 1. Database Contract: `approved_content_record` & `translation_output`

To manage multi-lingual translations and prevent stale caches after edits, the repository defines two main tables in `data/canonical.db`.

### 1.1 Table Schema: `approved_content_record` (Shared Handoff Artifact - Co-located)
Stores the finalized publication mother-draft ready for downstream modules. Exactly one row exists per approved source item.

* **Versioning Strategy (MVP)**:
  - This table only stores the latest canonical mother-draft per `source_item_id`.
  - Upstream edits or modifications replace/overwrite the existing handoff row (using `ON CONFLICT(source_item_id) DO UPDATE` or `REPLACE`).
  - Historical versions of the mother-draft are not retained as separate rows. Any upstream modification alters the `content_fingerprint`, which automatically invalidates downstream translations (forcing them to `stale` as detailed in the invalidation policy).

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `parent_content_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate primary key. Referenced as FK by downstream tables. |
| `source_item_id` | `INTEGER` | `NOT NULL UNIQUE` | FK to `source_item(source_item_id) ON DELETE CASCADE`. Ensures 1 mother-draft per item. |
| `display_title` | `TEXT` | `NOT NULL` | Finalized de-sensationalized title (either direct curation or operator edited). |
| `content_body` | `TEXT` | `NOT NULL` | Spliced Markdown body text representing the finalized mother-draft content. |
| `content_fingerprint` | `TEXT` | `NOT NULL` | SHA-256 hash of normalized `display_title` and normalized `content_body` for change detection. This fingerprint is computed only by the upstream handoff assembler at write time and then treated as canonical by downstream consumers. |
| `content_language_code` | `TEXT` | `NOT NULL` | The language code of the finalized mother-draft (e.g. `'zh'`, `'en'`), computed and written only by the upstream handoff assembler at write time. |
| `approved_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 business timestamp when curation approval or editing was finalized. This is the canonical editorial approval/publication time preserved for downstream consumers. |
| `author_metadata` | `TEXT` | `NULL` | JSON string representing author metadata. For the MVP, this must contain `source_module` and `writer_type` (e.g., `'AI'`, `'human'`, `'hybrid'`). Conditional schema rule: when `writer_type` is `'human'` or `'hybrid'`, it must also contain a non-empty `editor` field designating human responsibility. When `writer_type` is `'AI'` or `'machine'`, the `editor` field is optional. In the current implementation (curated via pure API), `writer_type` defaults to `'AI'`. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 system timestamp for when this handoff row was first materialized in `approved_content_record`. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 system timestamp for when this handoff row was last refreshed in `approved_content_record`. |

### 1.2 Table Schema: `translation_output` (Translate Module)
Stores translated outputs grouped by language code and parent draft identifier.

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `translation_output_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate primary key. |
| `parent_content_id` | `INTEGER` | `NOT NULL` | FK to `approved_content_record(parent_content_id) ON DELETE CASCADE`. |
| `source_item_id` | `INTEGER` | `NOT NULL` | FK to `source_item(source_item_id)`. Retained for join queries and auditing. |
| `language_code` | `TEXT` | `NOT NULL` | The target language code (e.g., `'zh'`, `'en'`, `'ja'`). |
| `display_title` | `TEXT` | `NULL` | The translated title. Nullable to support initial failure states before first translation success. |
| `content` | `TEXT` | `NULL` | Spliced Markdown body text. Nullable to support initial failure states before first translation success. |
| `source_fingerprint` | `TEXT` | `NOT NULL` | The canonical fingerprint copied from the upstream `approved_content_record.content_fingerprint`. |
| `translation_status` | `TEXT` | `NOT NULL` | Lifecycle state: `'pending'`, `'completed'`, `'failed'`, `'stale'`. |
| `retry_count` | `INTEGER` | `NOT NULL DEFAULT 0` | Count of failed attempts. Logical lock applies when status='failed' AND retry_count >= retry_attempts (configured in config/model_settings.yaml, defaulting to 3). |
| `model_name` | `TEXT` | `NOT NULL` | Name/ID of the LLM used for translation. |
| `prompt_version` | `TEXT` | `NOT NULL` | Version identifier of the prompt template used. |
| `translated_at` | `TEXT` | `NULL` | UTC ISO-8601 timestamp when translation was successfully completed. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

- **Unique Constraint**: `UNIQUE (parent_content_id, language_code)` ensures exactly one row exists per language code for each approved mother-draft.

### 1.3 Logical Storage Expectations
The eventual module migration should preserve these logical requirements:

- `translation_output` remains keyed by `parent_content_id` and `language_code`.
- `language_code` should not be treated as permanently limited to a fixed target language set at the contract level.
- `source_fingerprint` stores a snapshot of the upstream canonical fingerprint copied from `approved_content_record.content_fingerprint`.
- `translation_status` must support at least `pending`, `completed`, `failed`, and `stale`. The physical status column does not include a separate `'locked'` string; locked tasks are represented logically by `translation_status = 'failed'` AND `retry_count >= retry_attempts` (where `retry_attempts` is configured in `config/model_settings.yaml`).
- The storage layer must support efficient lookup by `(parent_content_id, language_code)` and by translation status.

### 1.4 SQLite DDL Migration Specification
This migration script will reside in `modules/translate/src/migrations/v001_initial_translate_tables.sql`:

```sql
PRAGMA foreign_keys = ON;

-- 1. approved_content_record (Shared Handoff Capability - Co-located)
CREATE TABLE IF NOT EXISTS approved_content_record (
    parent_content_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    display_title TEXT NOT NULL,
    content_body TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    content_language_code TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    author_metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_approved_content_record_source_item_id 
    ON approved_content_record(source_item_id);

CREATE INDEX IF NOT EXISTS idx_approved_content_record_fingerprint 
    ON approved_content_record(content_fingerprint);

-- 2. translation_output (Translate Module)
CREATE TABLE IF NOT EXISTS translation_output (
    translation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_content_id INTEGER NOT NULL,
    source_item_id INTEGER NOT NULL,
    language_code TEXT NOT NULL,
    display_title TEXT,
    content TEXT,
    source_fingerprint TEXT NOT NULL,
    translation_status TEXT NOT NULL CHECK (translation_status IN ('pending', 'completed', 'failed', 'stale')),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    translated_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_content_id) REFERENCES approved_content_record (parent_content_id) ON DELETE CASCADE,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id),
    UNIQUE (parent_content_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_translation_output_parent_lang 
    ON translation_output(parent_content_id, language_code);

CREATE INDEX IF NOT EXISTS idx_translation_output_status 
    ON translation_output(translation_status);
```

### 1.5 Handoff Materialization Rules

- `approved_content_record` is a materialized shared handoff artifact, not a live view over upstream editorial tables.
- The upstream assembler is solely responsible for constructing `display_title`, `content_body`, `content_fingerprint`, `content_language_code`, `approved_at`, `created_at`, and `updated_at` before downstream pull-based consumption.
- The assembler determines `content_language_code` for the mother-draft using this priority order: first, use an explicit finalized language field provided by the upstream `curate` or `edit` output when available; second, fall back to the corresponding `classification_result.primary_language_code` linked to the `source_item_id`; third, if neither upstream source provides a language value, run a deterministic language detection fallback on the assembled mother-draft text and persist the detected result.
- If the language still cannot be resolved confidently after applying the priority order above, the assembler must not silently default to an arbitrary language code; it must surface the item for operator review or follow an explicitly documented upstream fallback policy.
- The assembler may be physically co-located under `modules/translate/`, but it must remain implementation-independent from translation runtime logic and should not import translation-specific code.
- `approved_at` must be copied and preserved in the handoff row even if it is derivable from current upstream tables, because upstream editorial storage and retention policies may later diverge from downstream historical needs.
- `created_at` and `updated_at` are system materialization timestamps and must not be used as substitutes for the editorial meaning of `approved_at`.
- In the current MVP, the assembler may use the effective upstream finalized row's `updated_at` as its freshness signal for delta pre-screening when no separate `finalized_at`-style field exists.
- This means the current upstream contract assumes the effective `updated_at` of the selected finalized row changes whenever the downstream-visible mother-draft payload or its approval state changes.

---

## 2. Invalidation and Fingerprinting Policy

Detailed state transition definitions and cache invalidation matrices are defined in [STATE_TRANSITIONS.md](./STATE_TRANSITIONS.md). 

### 2.1 Upstream Fingerprint Alignment
The single source of truth for the mother-draft state version is `approved_content_record.content_fingerprint`. 
- During processing, `translate` reads the canonical `content_fingerprint` from the upstream `approved_content_record` and stores it directly in the `source_fingerprint` column of `translation_output`.

### 2.1.1 Fingerprint Computation Rules

- The canonical fingerprint is computed when the upstream handoff row is inserted or updated, not during downstream translation reads.
- The assembler must normalize line endings before hashing by converting all `\r\n` and bare `\r` sequences to `\n`.
- The assembler should use one stable serialization rule for the fingerprint input, for example `display_title + "\n\n" + content_body`, and apply it consistently for both inserts and updates.
- The `translate` runner must never recompute the source fingerprint from raw text for normal stale detection; it compares the stored upstream `content_fingerprint` against `translation_output.source_fingerprint`.
- As a result, steady-state invalidation work scales with changed rows rather than requiring full-table hash recomputation on every run.

### 2.1.2 Delta Detection Rules For The Assembler

- The assembler performs delta detection in two stages: a timestamp-based pre-screen followed by payload re-assembly and fingerprint confirmation.
- In the MVP contract, when the upstream module does not expose a dedicated `finalized_at` or `version_approved_at` field, the assembler should compare the effective upstream finalized row's `updated_at` against `approved_content_record.updated_at`.
- If no `approved_content_record` row exists for the `source_item_id`, the assembler must assemble the payload, compute the fingerprint, and insert the row.
- If `upstream.updated_at` is later than `approved_content_record.updated_at`, the assembler should treat the item as a candidate refresh, re-assemble the payload, and recompute the fingerprint.
- If `upstream.updated_at` is not later than `approved_content_record.updated_at`, the assembler may skip that item during normal delta runs.
- Timestamp comparison is only a pre-screen optimization. The authoritative content-drift check remains the recomputed payload fingerprint for candidate rows.
- `approved_at` remains the editorial approval timestamp of the current canonical version and must not be repurposed as the primary delta-comparison field.

### 2.2 Invalidation Conditions
A translation record is marked as `stale` or undergoes re-execution if:
1. **Fingerprint Mismatch**: The current `content_fingerprint` in the upstream `approved_content_record` does not match the stored `source_fingerprint` in `translation_output` (indicating the mother-draft was edited).
2. **Configuration Change**: The running config's `model_name` or `prompt_version` differs from the record's values (indicating a model update or prompt template change). Bypassed self-translations, identified by `model_name = 'bypass'` and `prompt_version = 'bypass'`, are exempt from this invalidation rule.
3. **Operator Overrule**: An operator manually triggers a retry, setting the record back to `pending`.

See [STATE_TRANSITIONS.md](./STATE_TRANSITIONS.md) and [EXECUTION_POLICY.md](./EXECUTION_POLICY.md) for retry details, error behaviors, and workflow constraints.
