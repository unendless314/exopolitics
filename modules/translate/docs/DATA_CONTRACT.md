# Translate Data Contract

**Document version:** v1.0  
**Updated:** 2026-06-18  
**Status:** Active planning & design

> [!NOTE]
> Detailed code-level properties documented here represent **current design proposals and working assumptions** to guide the MVP implementation, rather than locked top-level system contracts. In particular, target language sets and concrete storage DDL may evolve as multilingual requirements expand.

---

## 1. Database Contract: `translation_output`

To manage multi-lingual translations and prevent stale caches after edits, the `translate` module writes to the `translation_output` table in `data/canonical.db`.

### 1.1 Table Schema: `translation_output`

| Field Name | SQLite Type | Nullability | Description / Constraint |
| :--- | :--- | :--- | :--- |
| `translation_output_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Surrogate primary key. |
| `parent_content_id` | `INTEGER` | `NOT NULL` | FK to `approved_content_record(parent_content_id) ON DELETE CASCADE`. |
| `source_item_id` | `INTEGER` | `NOT NULL` | FK to `source_item(source_item_id)`. Retained for join queries and auditing. |
| `language_code` | `TEXT` | `NOT NULL` | The target language code (e.g., `'zh'`, `'en'`, `'ja'`). |
| `display_title` | `TEXT` | `NOT NULL` | The translated title for the target language. |
| `content` | `TEXT` | `NOT NULL` | Spliced Markdown body text translated into target language. |
| `source_fingerprint` | `TEXT` | `NOT NULL` | The canonical fingerprint copied from the upstream `approved_content_record.content_fingerprint`. |
| `translation_status` | `TEXT` | `NOT NULL` | Lifecycle state: `'pending'`, `'completed'`, `'failed'`, `'stale'`. |
| `model_name` | `TEXT` | `NOT NULL` | Name/ID of the LLM used for translation. |
| `prompt_version` | `TEXT` | `NOT NULL` | Version identifier of the prompt template used. |
| `translated_at` | `TEXT` | `NULL` | UTC ISO-8601 timestamp when translation was successfully completed. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

- **Unique Constraint**: `UNIQUE (parent_content_id, language_code)` ensures exactly one row exists per language code for each approved mother-draft.

### 1.2 Logical Storage Expectations
The eventual module migration should preserve these logical requirements:

- `translation_output` remains keyed by `parent_content_id` and `language_code`.
- `language_code` should not be treated as permanently limited to a fixed three-language set at the contract level.
- `source_fingerprint` stores the upstream canonical fingerprint copied from `approved_content_record.content_fingerprint`.
- `translation_status` must support at least `pending`, `completed`, `failed`, and `stale`.
- The storage layer should support efficient lookup by `(parent_content_id, language_code)` and by translation status.

Concrete SQLite DDL and migration filenames should be finalized alongside the actual module implementation.

---

## 2. Invalidation and Fingerprinting Policy

To handle editorial changes, prompt upgrades, and LLM parameter shifts without displaying stale translations, the system enforces fingerprinting verification.

### 2.1 Upstream Fingerprint Alignment
The single source of truth for the mother-draft state version is `approved_content_record.content_fingerprint`. 
- The `translate` module does not compute its own independent content fingerprint.
- During processing, `translate` reads the canonical `content_fingerprint` from the upstream `approved_content_record` and stores it directly in the `source_fingerprint` column of `translation_output`.

### 2.2 Invalidation Conditions
A translation record is marked as `stale` or undergoes re-execution if:
1. **Fingerprint Mismatch**: The current `content_fingerprint` in the upstream `approved_content_record` does not match the stored `source_fingerprint` in `translation_output`.
2. **Configuration Change**: The running config's `model_name` or `prompt_version` differs from the record's values.
3. **Operator Overrule**: An operator manually triggers a retry, setting the record back to `pending`.

### 2.3 Translation Lifecycle State Matrix

> [!NOTE]
> The downstream behavior for the `publish` module described below represents current working assumptions under discussion and is not a formal contract enforced by the `translate` module.

| State (Status) | Description | Downstream `publish` behavior (Working Assumptions) |
| :--- | :--- | :--- |
| **`pending`** | Task registered/queued, awaiting LLM translation call. | Excluded from static generation (unless fallback is enabled). |
| **`completed`** | Translation successfully performed and fingerprints match. | Eligible for static file generation. |
| **`failed`** | Encountered API timeout, prompt refusal, or schema syntax error. | Excluded from static generation; re-tried in next execution run. |
| **`stale`** | Upstream mother-draft or translation config changed. | Triggers re-translation. In interim, downstream may fallback or hide. |
