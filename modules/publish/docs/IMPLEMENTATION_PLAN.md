# Publish Implementation Plan

**Document version:** v2.0  
**Updated:** 2026-06-24  
**Status:** Active rewrite draft

---

## 1. Implementation Goals

The active implementation should deliver a production-usable `publish` module that:

- reads the current translate output already persisted in the database
- materializes stable multilingual static exports for the site layer
- correctly synchronizes withdrawals and eligibility loss
- remains fully rebuildable from canonical data

---

## 2. Phased Work

### Phase 1: Schema And Repository Layer

- add `modules/publish/src/migrations/v001_initial_publish_tables.sql`
- implement repository helpers for `publish_record` and `publish_language_status`
- implement deterministic slug lookup and creation helpers

### Phase 2: Export Eligibility And Reconciliation

- implement the query path that reads `approved_content_record`, `translation_output`, `curation_decision`, and `source_item`
- group rows by `source_item_id`
- enforce the configured coverage policy, starting with `strict_match`
- detect previously published rows that should now be withdrawn from the export layer

### Phase 3: File Emission

- write item JSON files under `data/publish_export/<language>/items/`
- rebuild `index.json` (latest $N$ items) and affected `archives/archive_YYYY_MM.json` monthly archives
- rebuild monthly archive index manifest `archives/index.json`
- generate `stats.json` with archive observation metrics

### Phase 4: CLI And Operational Commands

- implement `validate`
- implement `migrate`
- implement `run`
- implement `rebuild`
- implement `status`

---

## 3. Test Focus

Tests should cover at least the following:

1. Slug creation, collision handling, and slug freezing across later republishes.
2. Strict-match eligibility when one language is missing, failed, stale, or fingerprint-mismatched.
3. Withdrawal synchronization when upstream `curate_status` changes from `approved` to `withdrawn`.
4. Re-publication when a withdrawn item becomes approved again.
5. Rebuild correctness with pre-existing publish rows and frozen slugs, ensuring all monthly archives are fully reconstructed.
6. Idempotent reruns against unchanged database state.
7. Aggregate file generation excluding withdrawn items.
8. Historical archive withdrawal synchronization (when an item is withdrawn, ensure it is removed from both `index.json` and its specific monthly `archive_YYYY_MM.json` file, and that the manifest `archives/index.json` is updated).
9. Monthly archive rebuild correctness (validation of full directory clean rebuild vs. incremental execution granularity).
10. Latest index and monthly archive overlap consistency (verifying that items can exist in both `index.json` and `archive_YYYY_MM.json` as expected).

---

## 4. MVP Constraints

- no frontend rendering responsibilities belong here
- no direct database access from `site`
- no LLM usage belongs here
- no publish-layer override of editorial approval belongs here

---

## 5. Pre-Production Reset Policy

As with other active module rewrites in this repository, the current `publish` schema should be treated as pre-production.

For this phase:

- update the initial publish migration directly if needed
- do not preserve backward compatibility for earlier draft publish tables unless a real persisted environment requires it
