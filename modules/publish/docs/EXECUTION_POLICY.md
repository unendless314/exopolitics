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
9. Rebuild `index.json`, `feed.xml`, and `stats.json` from the final active published set.

This sequencing ensures aggregate files are built from the post-sync state rather than a stale intermediate snapshot.

---

## 4. Database Transactions And File Writes

- Long file-system work must not hold open SQLite write transactions.
- Network calls are not expected in this module under the current design.
- Short transactions should wrap only the row mutations needed for slug creation and publish status updates.
- If an item file write fails after a publish row was updated, the runner should surface the error and avoid finalizing aggregate files from a partially successful in-memory state.

Recommended safety model:

1. Compute export decisions in memory.
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
- no reappearance of withdrawn items in indexes or feeds
- optional avoidance of rewriting unchanged files when content bytes are identical

Idempotency matters more than micro-optimizing file writes in the current phase.

---

## 6. Rebuild Policy

The `rebuild` command must treat `data/publish_export/` as disposable output.

Required behavior:

- clear or recreate the export directory
- reload canonical publish eligibility from the database
- reuse existing frozen slugs from `publish_record`
- regenerate all item files, indexes, feeds, and stats from scratch
- keep withdrawn items absent from the rebuilt output

The rebuild command must not fabricate new slugs for source items that already have `publish_record` rows.

---

## 7. Validation Rules

### 7.1 Configuration & System Validation

Before executing synchronization, the runner must validate the active configuration:

- **Target Language Existence**: Every language code specified in the publish configuration's target/public languages must exist in the database as a recognized translation output (i.e. present in `translation_output.language_code`). If a configured language has no translation records in the database, the runner should abort execution (or issue a blocking warning if the database is completely empty on cold starts) to prevent system lockouts under strict coverage policies.

### 7.2 Artifact Validation

Before exporting an individual language artifact, the runner should validate at least:

- `display_title` is non-empty
- `content` is non-empty
- `translation_status = 'completed'`
- `translation_output.source_fingerprint = approved_content_record.content_fingerprint`
- upstream `curate_status = 'approved'`
- required route components (`language_code`, `slug`) are available
- `author_metadata` is present as a well-formed JSON string that parses to a JSON object containing at least `source_module` and `writer_type`; if the value is `NULL`, invalid JSON, not an object, or missing required keys, the runner must abort compilation for this item and raise a validation error.

If any of these fail, the artifact must not be exported.

---

## 8. Status Command Expectations

The `status` command should provide a concise publish-layer summary, for example:

- active published language artifacts
- withdrawn language artifacts
- total source items with frozen slugs
- source items currently eligible under the active coverage policy
- source items blocked by incomplete language coverage

This command should reflect publish-layer projection state, not attempt to redefine upstream editorial counts.
