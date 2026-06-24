# Publish Execution Policy

**Document version:** v1.0  
**Updated:** 2026-06-24  
**Status:** Active rewrite draft

---

## 1. Purpose

This document defines runner sequencing, selection behavior, transaction boundaries, rebuild rules, and idempotency requirements for the `publish` module.

---

## 2. Runner Scope

The runner operates over two related sets:

- the current eligible export set derived from upstream canonical tables
- the previously published set derived from `publish_record` plus `publish_language_status`

The runner must reconcile these sets to produce both new exports and cleanup actions.

---

## 3. Recommended Execution Sequence

For a normal `run`, the orchestrator should execute in this order:

1. Load publish configuration, including required public languages and coverage policy.
2. Query the current eligible export set.
3. Group rows by `source_item_id` and apply the language coverage policy.
4. Ensure a stable `publish_record.slug` exists for each exportable item.
5. Upsert `publish_language_status` rows for exportable language artifacts.
6. Write or overwrite item JSON files.
7. Detect previously published rows that are no longer exportable.
8. Remove obsolete item JSON files and mark those language rows as `withdrawn`.
9. Rebuild `index.json` and `stats.json` from the final active published set.

This sequencing ensures aggregate files are built from the post-sync state rather than a stale intermediate snapshot.

---

## 4. Database Transactions And File Writes

- Long file-system work must not hold open SQLite write transactions.
- Network calls are not expected in this module under the current design.
- Short transactions should wrap only the row mutations needed for slug creation and publish status updates.
- If an item file write fails after a publish row was updated, the runner should surface the error and avoid finalizing aggregate files from a partially successful in-memory state.

Recommended safety model:

1. Compute export decisions using lightweight metadata only, in memory or in bounded batches.
2. Perform short database upserts for the affected item or language row.
3. Write the concrete file artifact.
4. If a write fails, stop the run, report the failure, and avoid silently marking success for aggregate outputs.

---

## 5. Idempotency Requirements

Repeated runs against unchanged upstream state should be safe.

Expected properties:

- no duplicate `publish_record` rows
- no duplicate `publish_language_status` rows
- no slug regeneration for already-published items
- no reappearance of withdrawn items in indexes
- optional avoidance of rewriting unchanged files when content bytes are identical

Idempotency matters more than micro-optimizing file writes in the current phase.

---

## 6. Rebuild Policy

The `rebuild` command must treat `data/publish_export/` as disposable output.

Required behavior:

- clear or recreate the export directory
- reload canonical publish eligibility from the database
- reuse existing frozen slugs from `publish_record`
- regenerate all item files, indexes, and stats from scratch
- keep withdrawn items absent from the rebuilt output

The rebuild command must not fabricate new slugs for source items that already have `publish_record` rows.

---

## 7. Validation Rules

### 7.1 Configuration & System Validation

Before executing synchronization, the runner must validate the active configuration:

- **Target Language Existence**: Every language code specified in the publish configuration's target/public languages should ideally exist in the database as a recognized translation output (i.e. present in `translation_output.language_code`). 
  - **For the `validate` command**: If a configured target language has zero translation records in the database, the validator must issue a blocking validation failure.
  - **For the `run` and `rebuild` commands**: Instead of aborting execution completely (which makes early bootstrap environments fragile when some languages haven't been translated yet), the runner must issue a warning and exclude items from publication if the missing language blocks the configured coverage policy (e.g. `strict_match` will naturally prevent items from being published, but the process will exit gracefully with a warning rather than crashing or aborting). To prevent command-line output and log pollution, warnings for missing configured target languages must be emitted only once per missing language per command execution run, rather than repeatedly per evaluated item.

### 7.2 Artifact Validation

Before exporting an individual language artifact, the runner should validate at least:

- `display_title` is non-empty
- `content` is non-empty
- `translation_status = 'completed'`
- `translation_output.source_fingerprint = approved_content_record.content_fingerprint`
- upstream `curate_status = 'approved'`
- required route components (`language_code`, `slug`) are available
- `author_metadata` is required and must be present as a well-formed JSON string that parses to a JSON object containing at least `source_module` and `writer_type`. Under the conditional schema rule, if `writer_type` is `'human'` or `'hybrid'`, it must also contain a non-empty `editor` field. If the value is `NULL` in the database, invalid JSON, not an object, missing required keys, or violates this conditional rule, the runner must abort compilation for this item and raise a validation error.

If any of these fail, the artifact must not be exported.

---

## 8. Status Command Expectations

The `status` command should provide a concise publish-layer summary. To prevent terminal flooding when dealing with large datasets (thousands of items), the command must output **aggregated counts and statistics** rather than lists of individual items or full payloads.

The summary should display metrics such as:

- count of active published language artifacts
- count of withdrawn language artifacts
- count of total source items with frozen slugs
- count of source items currently eligible under the active coverage policy
- count of source items blocked by incomplete language coverage

Optional verbose flags (e.g. `--verbose` or `--limit`) may be implemented to inspect specific items, but the default behavior must remain lightweight and summary-only.

This command should reflect publish-layer projection state, not attempt to redefine upstream editorial counts.

---

## 9. Memory Management & Scalability Rules

To support high volume data growth (e.g. 100k+ source items) while reducing the risk of memory exhaustion (OOM) and avoiding unbounded resource growth, the runner must adhere to the following execution constraints:

### 9.1 Lightweight Reconciliation
- During the initial reconciliation, state check, and slug assignment phases, the runner **must not** query the large `content` (Markdown body) column from the database. The database queries for reconciliation must select only lightweight metadata fields (e.g., `source_item_id`, `parent_content_id`, `slug`, `language_code`, `publish_status`, `content_fingerprint`, `source_fingerprint`).

### 9.2 Chunked/Streaming File Emission
- When writing item JSON files to disk (especially during a full `rebuild` command), the runner **must not** load the entire dataset of content bodies into memory at once.
- The runner must process records in chunks (e.g., using paginated SQL queries or SQLite cursors with `fetchmany(1000)`). The memory footprint during file emission must be bounded by the chunk size and aggregate writer buffers, and must not scale linearly with the total number of published items.

### 9.3 Lightweight Index Compilation
- In this system, `summary_short` is a preview text extracted from the first paragraph (or a configured character limit) of the translated content body during compilation. Aggregate compilation for `index.json` may query `content` only when needed to derive this preview text. When `content` is read for aggregate compilation, the runner should still process rows in bounded batches rather than loading the full dataset at once. The condensed nature of the content reduces per-row cost, but does not remove the need for bounded processing at large scale.
- The primary language index (`index.json`) must remain lightweight by containing only metadata and short summaries. To avoid browser performance degradation when total dataset size grows extremely large, pagination or sharded index files should be the planned scaling path once active published item counts exceed a configured threshold.
