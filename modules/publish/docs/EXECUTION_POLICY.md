# Publish Execution Policy

**Document version:** v2.7
**Updated:** 2026-08-22
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

1. Acquire the single-writer process lock (see Section 4) and take the run's single logical timestamp; every timestamp written by the run uses this value.
2. Load publish configuration, including required public languages and coverage policy.
3. Query the current eligible export set.
4. Group rows by `source_item_id` and apply the language coverage policy.
5. Ensure a stable `publish_record.slug` exists for each exportable item.
6. Upsert `publish_language_status` rows for exportable language artifacts in short transactions.
7. Detect previously published rows that are no longer exportable and mark those language rows as `withdrawn` in short transactions.
8. Delete `publish_archive_metadata` rows of languages no longer configured (unconditional, during reconciliation).
9. Open one held database snapshot transaction (`BEGIN IMMEDIATE`, reserving the writer slot for the whole phase) and, inside it, build the deterministic generation plan from the post-sync database state and compare its `content_fingerprint` against `current.json` (see Section 6 for the build trigger rules). The snapshot covers the whole generation phase — plan build, fingerprint pass and write pass — so every artifact in a generation comes from exactly one database state.
10. If a build is triggered: build the complete new generation in staging and move it into `generations/`, sync `publish_archive_metadata` to the plan (the writes join the snapshot transaction, which commits before the pointer switch), atomically switch `current.json` (the commit point), then sweep retired generations.
11. If no build is triggered: atomically refresh the pointer's `last_successful_run_at` only.

This sequencing ensures aggregate files are built from the post-sync state rather than a stale intermediate snapshot, and that the pointer always records the fingerprint of the state it points at.

---

## 4. Database Transactions, File Writes, And Failure Model

- Long file-system work must not hold open SQLite write transactions. The one deliberate exception is the generation-phase read snapshot below: it holds a read (shared) lock, never a write lock, while files are written.
- Network calls are not expected in this module under the current design.
- Short transactions wrap only the row mutations needed for slug creation and publish status updates; the `publish_archive_metadata` sync after a completed generation build joins the held snapshot transaction and commits before the pointer switch.
- The generation phase (pointer read, plan build, fingerprint pass, write pass) runs inside one explicit SQLite transaction, so every artifact in a generation reflects exactly one database snapshot. The transaction is opened with `BEGIN IMMEDIATE` to reserve the writer slot up front: a concurrent upstream writer (curate/translate) fails at its own `BEGIN IMMEDIATE` with `SQLITE_BUSY`, instead of starting writes that would interleave into the snapshot or doom the metadata commit's shared-to-writer lock upgrade. Readers are unaffected (shared locks remain compatible), and the pipeline runs modules sequentially, so this never contends in normal operation.
- The whole run is serialized through a single-writer process lock at `publish_runner.lock` in the database file's directory (curate/translate precedent): non-blocking acquire, `RuntimeError` on contention. Release unlocks and closes the handle but deliberately leaves the lock file in place — deleting the path after unlocking would allow an inode-reuse race where two processes hold locks on different inodes of the same path. A stale lock file is harmless: the lock state lives on the inode, not the path.

Failure model (fail-stop):

- The pointer switch is the single commit point of a run. Readers see either the complete old generation or the complete new one, never partial output.
- A failure at any stage stops the run and leaves the live pointer untouched. There is no database compensation and no file-system rollback: the pre-generation `rollback_db_state`, per-file `.backup` copies, and per-file promotion machinery are deleted.
- After a failure the database may run ahead of the live generation (for example, publish rows committed, or archive metadata synced, before a build or pointer-switch failure). The next successful run converges by state comparison: its plan fingerprint differs from the pointer's, so it builds and switches.
- A corrupt `current.json`, or a missing or corrupt `meta.json` on the live generation, is a manual-intervention state: the run fails stop instead of silently rebuilding.
- Retention runs only after a successful pointer switch and never fails an otherwise successful run (see Section 6.3).

Recommended safety model:

1. Compute export decisions using lightweight metadata only, in memory or in bounded batches.
2. Perform short database upserts for the affected item or language row.
3. Build the deterministic generation plan from the committed snapshot and compare fingerprints.
4. Build the complete generation in staging; only then sync archive metadata and switch the pointer.
5. If any step fails, stop the run, report the failure, and rely on the next successful run to converge.

---

## 5. Idempotency Requirements

Repeated runs against unchanged upstream state should be safe.

Expected properties:

- no duplicate `publish_record` rows
- no duplicate `publish_language_status` rows
- no slug regeneration for already-published items
- no reappearance of withdrawn items in indexes
- a run against unchanged upstream state builds no new generation: every artifact of the live generation stays byte-identical (including `stats.json`, whose `last_export_run_timestamp` is frozen at the generation's build time), and only the pointer's `last_successful_run_at` advances

Idempotency matters more than micro-optimizing file writes in the current phase.

---

## 6. Rebuild Policy

Both `run` and `rebuild` treat `data/publish_export/` as disposable output that is always rebuildable from canonical database state.

### 6.1 Build Trigger And Generation Granularity
- **Incremental Run (`run` command)**:
  - After reconciliation commits the database state, the runner builds a deterministic generation plan from the post-sync snapshot and compares its `content_fingerprint` against `current.json`. A new generation is built only when the fingerprint differs or when no pointer exists (bootstrap — the first successful run always builds a complete, possibly empty, generation).
  - A no-change run builds nothing and only refreshes the pointer's `last_successful_run_at` atomically.
  - When a build does happen it is always a complete generation — every item JSON, the latest `index.json`, the archives manifest (`archives/index.json`, always written, empty as `[]`), all monthly archive files, `stats.json`, and `meta.json` — built from the full active published set. There is no affected-months-only file emission.
  - The physical write policy for a `run` build: an unchanged artifact whose planned digest matches the trusted live generation's hash-stream record is reused from that generation via an `os.link()` hardlink — the source must be a regular, non-reparse file inside the trusted prior generation, every link is verified after the fact (destination and source must resolve to the same `(st_dev, st_ino)`; a mismatch removes the destination and fails stop), and any link failure (cross-volume placement, filesystem policy, NTFS limitation, network storage) falls back to a safe physical write of the planned bytes. The reuse decision trusts the hashes recorded in the prior generation's stream and never re-hashes source bytes; the accepted residual integrity risk (out-of-band corruption propagating through hardlinks) is repaired by `rebuild`. Generation contents are immutable after creation — safety-critical under hardlink reuse, since an in-place edit would silently rewrite every generation sharing the inode (see DATA_CONTRACT.md Section 6.7).
  - Manifest and statistics counts must still be derived from SQL aggregation queries directly over the SQLite canonical tables (`publish_record` and `publish_language_status`); the runner must not load, parse, or scan historical monthly archive files from disk to compute these metrics. Archive `updated_at` stamping consults the live generation's hash stream (`file_hashes.jsonl`), falling back to a digest-compare against the fallback root, without scanning the whole tree (see DATA_CONTRACT.md Section 2.3).
- **Full Rebuild (`rebuild` command)**:
  - `rebuild` always builds a complete new generation, regardless of the fingerprint comparison, and switches the pointer to it. Old generations are reclaimed by retention (see Section 6.3), not by clearing the export directory.
  - `rebuild` always performs a full physical rewrite of every artifact: hardlink reuse is disabled even when hashes match. This is the escape hatch for serializer changes, hash-algorithm upgrades, and repair operations (including repair of corruption propagated through hardlinks), re-establishing verified bytes.
  - It must reload canonical publish eligibility from the database.
  - It must reuse existing frozen slugs from `publish_record`.
  - It must keep withdrawn items absent from all rebuilt outputs.
  - The rebuild command must not fabricate new slugs for source items that already have `publish_record` rows.
  - Every active month's archive metadata is restamped with the rebuild run's logical clock, and rows for months with no active items are removed. The pointer records the fingerprint of the restamped plan, so the next no-change `run` does not build again.

### 6.2 Configured Language-Set Changes

Changing `target_languages` is not a display-only setting: the next `run` or `rebuild` reconciles public artifacts against the new configured set under the active coverage policy.

- Shrinking the language set withdraws the `publish_language_status` rows of the removed languages and deletes their `publish_archive_metadata` rows unconditionally during reconciliation; the next generation simply contains no artifacts for the removed languages — no item JSON files, no latest `index.json`, no monthly archive files, no archives manifest — and the pointer's `languages` list shrinks with it. Artifacts, archive metadata and publish timestamps of the remaining languages are left unchanged. Directory names and generic subdirectory shapes are **not** ownership evidence: a leftover language-named directory at the export root is inert — runs never clean it or let it leak into a generation — so artifacts left behind after a canonical database reset must be cleared by wiping the derived export tree before the next run rather than by heuristic sweeps. Symlink/junction safety applies to retention's deletion of old generations (see Section 6.3).
- Expanding the language set applies `strict_match` against the enlarged set: items lacking a completed current-fingerprint translation for a newly required language are withdrawn in all languages until coverage is complete; items already fully translated gain the new language's artifact while their existing languages remain published with unchanged timestamps.

### 6.3 Generation Retention

`generations/` keeps the newest 5 generation directories and always protects the generation the live pointer references, even in pathological orderings. "Newest" is chronological: the timestamp portion sorts lexicographically (ISO zero-padding is chronological) and the same-second `-rN` collision suffix sorts numerically — a plain string sort would order `-r10` before `-r2`. Symmetrically, same-second suffixes are allocated after the highest surviving suffix, never refilling gaps left by retention, so a fresh build's id always sorts newest and a later sweep cannot mistake it for an old one. Retention runs only after a successful pointer switch. A generation that is, or contains, a symlink or junction is skipped with a warning (deletion never follows links), and deletion failures (for example files held open by a reader) are warn-only and retried on a future run — retention never fails an otherwise successful run.

### 6.4 Bootstrap

- **Bootstrap**: when no pointer exists, the first successful run always builds a complete, possibly empty, generation and establishes the pointer (see DATA_CONTRACT.md Section 6 for the empty-generation layout).
- A pre-pointer flat export tree (recognized by a root `stats.json`) is not recognized as publish state: the run simply bootstraps from the database. Any flat residue stays inert at the export root — the site reads only through `current.json` — and can be deleted manually.

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
- the item payload passes `validate_item_payload()` before any item JSON is written:
  - `summary_short` is a string that remains non-empty after trimming leading and trailing whitespace
  - when `downstream_action = 'publish_summary'`, `bullets` is an object containing exactly the keys `key_claim`, `evidence_level`, and `objective_impact`, and each value is a string that remains non-empty after trimming leading and trailing whitespace
  - when `downstream_action = 'publish_link'`, `bullets` is JSON `null`
  - any other `downstream_action` value must not pass publish payload validation
- `translation_status = 'completed'`
- `translation_output.source_fingerprint = approved_content_record.content_fingerprint`
- upstream `curate_status = 'approved'`
- required route components (`language_code`, `slug`) are available
- `author_metadata` is required and must be present as a well-formed JSON string that parses to a JSON object containing at least `source_module` and `writer_type`. `source_module` must be a string that remains non-empty after trimming whitespace. Under the conditional schema rule, if `writer_type` is `'human'` or `'hybrid'`, it must also contain an `editor` field under the same trim-non-empty string rule; neither field may be coerced from another JSON type. If the value is `NULL` in the database, invalid JSON, not an object, missing required keys, or violates these type or conditional rules, the runner must abort compilation for this item and raise a validation error.

If any of these fail, the artifact must not be exported.

Upstream `curate` already enforces the 0-or-3 bullet invariant (all three bullets present for `publish_summary`, all three `NULL` for `publish_link`), but `publish` must re-validate it independently at the payload boundary and must not defer malformed payloads to the `site` build.

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
- During the initial reconciliation, state check, and slug assignment phases, the runner **must not** fetch per-item payload fields (`display_title`, `summary_short`, `bullet_1`, `bullet_2`, `bullet_3`). The database queries for reconciliation must select only lightweight metadata fields (e.g., `source_item_id`, `parent_content_id`, `slug`, `language_code`, `publish_status`, `content_fingerprint`, `source_fingerprint`).

### 9.2 Chunked/Streaming File Emission
- When writing item JSON files to disk (especially during a full `rebuild` command), the runner **must not** load the entire dataset of item payloads into memory at once.
- The runner must process records in chunks (e.g., using paginated SQL queries or SQLite cursors with `fetchmany(1000)`). The memory footprint during file emission must be bounded by the chunk size and aggregate writer buffers, and must not scale linearly with the total number of published items.
- The same chunked streaming applies to the export-state fingerprint pass that precedes every build decision: planned artifacts are read and hashed in bounded batches without being written to disk, so state comparison does not change these bounds.
- Artifact-hash bookkeeping follows the same bound. The planned per-artifact digests are carried from the fingerprint pass to the write pass through a disk-backed temporary digest index (a temporary SQLite file at the export root, appended in bounded batches, consumed in fixed artifact order, and discarded at run teardown); the prior generation's `file_hashes.jsonl` hash stream is streamed line by line into that index; and the new generation's stream is appended record by record during the write pass. No structure proportional to item count — payloads or hashes — is ever resident in memory.

### 9.3 Lightweight Index Compilation
- `summary_short` is a short upstream field read directly from `translation_output.summary_short`; no summary is extracted or derived from a larger body field, and no body-derived fallback exists. Aggregate compilation for `index.json` and monthly `archive_YYYY_MM.json` files therefore reads only lightweight fields, and must still process rows in bounded batches (e.g. chunked SQLite queries) rather than loading the full result set at once.
- The primary language index (`index.json`) must remain lightweight by containing only metadata and short summaries. To avoid browser performance degradation when the total dataset size grows extremely large, the system adopts a dual-track indexing strategy (Latest N items `index.json` + Monthly Archive `archive_YYYY_MM.json`). This replaces quantity-based index sharding or pagination. Memory footprint during aggregate index/archive compilation must be bounded by batch size and must not scale linearly with total records.

