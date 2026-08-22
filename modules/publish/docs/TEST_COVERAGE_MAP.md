# Publish Test Coverage Map

**Document version:** v1.10
**Updated:** 2026-08-22
**Status:** Active
**Source:** `known_issues/resolved/PUBLISH_TEST_MAINTAINABILITY_PLAN.md` Phase 0 deliverable

---

## 1. Purpose

This map lists every publish test, the contract rule it protects, and its unique
assertions. It is the baseline for the consolidation rules in
`PUBLISH_TEST_MAINTAINABILITY_PLAN.md` section 7.4: no existing test may be
deleted unless a replacement test protects the same rule and no unique
assertion is lost. Every removed node ID must name its replacement node ID.

Focus areas 1-10 refer to `IMPLEMENTATION_PLAN.md` section 3 ("Test Focus").

Shared harness: `tests/support.py` provides the five-column upstream schema,
`make_config()`, `seed_item()`, artifact readers and `FakeClock`. Artifact
readers resolve the live generation through the `current.json` pointer
(`live_root()` / `read_pointer()`), so tests assert against the same layout
readers see. Run-flow failure injection patches
`modules.publish.src.generation_store.write_generation_to_staging` (generation
build failure) or `modules.publish.src.generation_store.write_pointer_atomic`
(pointer-switch failure); the pointer atomicity unit tests patch `os.replace`
directly. The harness carries no assertions of its own; the real-migration
schema contract lives in `test_handoff_contract.py` (section 13).

---

## 2. `tests/test_publish.py` — `TestPublishModule`

| Node ID | Focus | Protected rule | Unique assertions (not covered elsewhere) |
| :--- | :--- | :--- | :--- |
| `test_slug_generation_and_freezing` | 1 | slug creation, collision suffix, frozen slug across republish | unit-level `slugify()` / `generate_slug()` results (incl. `slugify("中文") == ""`) |
| `test_strict_match_eligibility` | 2 | strict-match blocks first publication when a language is missing / failed / stale / fingerprint-mismatched | four blocking causes in one matrix against first publication |
| `test_withdrawal_and_republication` | 3, 4 | upstream withdrawal removes artifacts and marks rows withdrawn; re-approval republishes | preserved `source_fingerprint` and `withdrawn_at` on withdrawn rows |
| `test_rebuild_and_idempotency` | 5, 6, 7 | rebuild reuses frozen slugs and excludes withdrawn items from aggregates | rebuild after an incremental withdrawal does not re-withdraw (`withdrawn_count == 0`) |
| `test_archive_withdrawal_and_overlap` | 8, 9, 10 | withdrawal rewrites monthly archive, deletes empty archive, updates manifest; index/archive overlap is intended | empty-archive file deletion; manifest month removal; stats active/withdrawn counts |
| `test_validation_errors` | — | author metadata human/hybrid editor rejection; CLI success surface | **unique:** CLI `validate` / `migrate` / `status` / `run` / `rebuild` exit codes; `status` blocked-item counting |
| `test_first_time_publish_build_failure_converges` | — | a generation-build failure on first publish keeps the new publish rows (no compensation, DB ahead) and establishes no live pointer; the next successful run converges by state comparison (replaces `test_first_time_file_write_compensation`, which pinned DB compensation deleting the new `publish_record`) | **unique:** first-publish build failure leaves the publish rows intact and converges on rerun |
| `test_warning_per_command_scope` | — | missing target language warning emitted once per run | **unique:** warning count per command execution, across two consecutive runs |
| `test_update_build_failure_converges` | — | an update-run build failure keeps the new DB state and the old live generation; the next run rebuilds from the DB state and a further no-change run stays on it (replaces `test_update_file_write_compensation`, which pinned restoration of the prior fingerprint/status) | **unique:** fingerprint convergence after the recovery build — no spurious extra build |
| `test_direct_rebuild_after_upstream_withdrawal` | 5, 8 | direct `rebuild` (no preceding incremental run) synchronizes withdrawal | rebuild-only withdrawal path |
| `test_failed_rebuild_leaves_live_generation_untouched` | — | a rebuild whose generation build fails must not clear or corrupt the live generation (renamed from `test_rebuild_file_write_failure_divergence_prevention`) | **unique:** live pointer and full generation byte snapshot survive a failed rebuild |
| `test_archive_index_batching_limit` | 9 (partial) | `index.json` truncated at `latest_limit` | only latest-limit truncation; does **not** cross a batch boundary (covered by section 11) |
| `test_pointer_switch_failure_converges` | — | a pointer-switch failure after the generation was built and the archive metadata applied leaves the DB ahead with the old generation still live; the next successful run converges both (replaces `test_promotion_midway_failure_reversion`, which pinned export-tree and DB rollback) | **unique:** live-generation byte snapshot and pointer stay intact after a pointer-switch failure; the convergence run rebuilds and switches |

## 3. `tests/test_item_payload_contract.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `TestItemPayloadSchemaFixtures::*` (4 tests) | payload contract | fixture/schema self-consistency under JSON Schema draft 2020-12 | **unique:** schema validity, valid/invalid fixture conformance, fixture-set completeness against `INVALID_FIXTURE_ERROR_TOKENS` |
| `TestValidateItemPayloadRules::*` (2 tests) | payload contract | `validate_item_payload()` accepts valid fixtures and rejects each invalid fixture with field context | **unique:** direct validator matrix over the 12 invalid JSON fixtures with per-fixture error tokens |
| `TestStructuredContentExport::test_semantic_mapping_publish_summary` | payload contract | `bullet_1/2/3` map exactly once to `key_claim`/`evidence_level`/`objective_impact`; no monolithic `content` key | exact zh/en bullet mapping end to end |
| `TestStructuredContentExport::test_publish_link_bullets_null` | payload contract | `publish_link` exports `bullets: null` (key present, never omitted, never an empty object) | both languages |
| `TestStructuredContentExport::test_summary_short_passthrough_to_index_and_archive` | payload contract | `summary_short` passes through verbatim to item, index, and archive | archive-entry-level assertion |
| `TestStructuredContentExport::test_extract_summary_short_removed` | payload contract | no body-derived summary fallback remains | guards against reintroducing `extract_summary_short` |
| `TestStructuredContentExport::test_exported_values_contain_no_ui_labels` | payload contract | no presentation UI label prefix leaks into any exported string | scans item JSON and index.json with a vacuous-pass guard |
| `TestFiveColumnSeedRegressions::test_strict_match_blocks_then_publishes_publish_link` | 2, payload | strict-match applies to `publish_link`; recovery exports `bullets: null` | failed-translation recovery path for link items |
| `TestFiveColumnSeedRegressions::test_frozen_slug_and_bullet_update_propagation` | 1 | slug frozen while fingerprint change re-exports updated bullets | updated bullet content observable in re-exported item |
| `TestFiveColumnSeedRegressions::test_author_metadata_rule_independent_of_bullet_shape` | payload | editor rule binds `publish_link` (null bullets); machine writer exports AI disclosure note | machine disclosure note end to end |

## 4. `tests/test_idempotency.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `test_unchanged_rerun_preserves_state_and_bytes` | 6, 7 | a second unchanged incremental run publishes/withdraws nothing and builds nothing: every artifact of the live generation stays byte-identical (`stats.json` included, its `last_export_run_timestamp` frozen at the generation's build time), every DB row is frozen, and only the pointer's `last_successful_run_at` advances | full live-generation byte snapshot plus publish-table snapshot across reruns; pointer field stability/advance via `FakeClock` |

## 5. `tests/test_index_archive_sorting.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `test_index_and_archive_sort_by_source_time_then_slug` | 8 | index and archives order by `source_published_at` DESC, slug ASC tiebreak | exact expected ordering with same-second timestamps in both index and archive |
| `test_sort_uses_source_time_not_publish_time` | 8 | ordering key is `source_item.published_at`, never the publish-layer `published_at` | **unique:** inverted publish-time vs source-time order proves the key |
| `test_latest_limit_truncates_after_full_sort` | 9 | `latest_limit` cuts the fully sorted list, not an arbitrary subset | truncated members are exactly the top-N of the sorted order |

## 6. `tests/test_coverage_loss.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `TestCoverageLossWithdrawal::test_withdrawal_after_language_fails` | 3 | completed-translation loss (`failed` status) withdraws every language artifact of the item; recovery republishes under the frozen slug | full withdrawal of aggregates (index/archive/manifest/stats) plus `published_at` preserved through withdrawal, per cause |
| `TestCoverageLossWithdrawal::test_withdrawal_after_translation_row_removed` | 3 | same rule when the translation row is deleted | row-removal cause with targeted re-INSERT recovery |
| `TestCoverageLossWithdrawal::test_withdrawal_after_fingerprint_goes_stale` | 3 | same rule when `source_fingerprint` no longer matches | stale-fingerprint cause |
| `TestConfiguredLanguageSetChanges::test_shrink_withdraws_only_removed_language` | 3, 8 | language-set shrink withdraws only the removed language; remaining languages keep artifacts, publish timestamps and archive metadata | **unique:** the live generation contains no directory for the removed language and the pointer's `languages` excludes it; its `publish_archive_metadata` rows are gone, not just its item files |
| `TestConfiguredLanguageSetChanges::test_shrink_rebuild_removes_removed_language_artifacts` | 5, 8 | a full rebuild under the shrunk set reconciles the removed language the same way | rebuilt generation contains none of the removed language's artifacts; remaining metadata refreshed with the rebuild clock |
| `TestConfiguredLanguageSetChanges::test_orphan_directory_without_publish_state_is_preserved` | 8 | directory names and generic subdirectory shapes (`items/`, `archives/`) are not ownership evidence: without `publish_language_status` rows a directory is never touched (replaces `test_orphan_language_directory_without_db_rows_removed`, which pinned the abandoned structural heuristic) | **unique:** `assets/items/customer.json` survives an empty run |
| `TestConfiguredLanguageSetChanges::test_unrelated_directory_is_left_alone` | 8 | a top-level directory with neither publish-owned rows nor the publish directory structure is not publish's to clean | **unique:** `assets/index.json` and non-pattern files survive an empty run |
| `TestRetentionLinkSafety::test_retention_skips_junction_generation` | 8 | retention never deletes through a retired-generation directory that is itself a junction/symlink: it is skipped with a warning and the link target's files are untouched (replaces `test_removed_language_junction_is_not_followed`; the pre-refactor removed-language sweep no longer exists) | **unique:** real junction (`mklink /J`) to a target outside the export tree; the other retiree is still deleted normally |
| `TestConfiguredLanguageSetChanges::test_shrink_allows_missing_removed_language_directory` | 3, 8 | shrink completes its database reconciliation when an operator has already removed the obsolete language directory from the live generation | **unique:** the absent path does not fail the shrink run |
| `TestRetentionLinkSafety::test_retention_skips_generation_containing_junction` | 8 | retention skips with a warning a retired generation that contains a linked subdirectory (replaces `test_removed_language_nested_junction_is_not_followed`) | **unique:** linked `items/` subdirectory inside an otherwise regular generation; the link target's payload is preserved |
| `TestConfiguredLanguageSetChanges::test_leftover_language_directory_at_export_root_is_inert` | 8 | a leftover language-named directory at the export root (outside `generations/`) is inert: runs neither clean it nor let it leak into the live generation or the pointer's `languages` (replaces `test_removed_language_lone_index_cleaned_via_publish_state`, which pinned the pre-refactor state-evidence sweep) | **unique:** root directory names are not ownership evidence; retired generations are reclaimed by retention, not heuristic sweeps |
| `TestConfiguredLanguageSetChanges::test_shrink_converges_after_build_failure` | — | a generation-build failure mid-shrink leaves the DB ahead (removed language withdrawn, its archive metadata deleted) while the pre-shrink generation stays live byte-identically; the next successful run converges and the new live generation has no removed-language artifacts (replaces `test_shrink_rolls_back_on_promotion_failure`, which pinned rollback) | **unique:** build failure injected on the shrink run's generation build |
| `TestConfiguredLanguageSetChanges::test_expand_with_incomplete_new_language_withdraws_item` | 3 | expanding the set under strict_match withdraws items lacking the new language until coverage completes | expand-driven withdrawal of all configured languages |
| `TestConfiguredLanguageSetChanges::test_expand_with_complete_new_language_adds_only_that_language` | 3 | expanding with complete coverage adds only the new language's artifact | existing languages keep artifacts and publish timestamps; `published_count == 1` |

## 7. `tests/test_aggregate_contracts.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `test_manifest_contract_fields_and_ordering` | 8, 10 | manifest entries carry `archive_month`/`file_name`/`item_count`/`updated_at`, order DESC by month, and every entry has a real file on disk | exact field-level manifest contract for both languages |
| `test_manifest_updated_at_lifecycle` | 8, 10 | `updated_at` moves only when its archive is (re)written: create, withdrawal-rewrite, empty-delete, recreate, rebuild | full timestamp lifecycle across five runs via `FakeClock`; metadata row deleted with empty archive |
| `test_missing_metadata_heals_by_restamping_archive_once` | 8, 10 | pre-v002 databases (archives without metadata rows) are healed by restamping the metadata once with that run's clock, which changes the planned manifest and therefore builds exactly one new generation; the archive bytes are unchanged and steady-state runs do not build again (renamed from `test_missing_metadata_heals_by_rewriting_archive_once`) | **unique:** the heal run builds exactly one new generation with identical archive bytes; the following no-change run builds nothing and does not advance the stamps |
| `test_archive_metadata_converges_after_pointer_switch_failure` | — | a pointer-switch failure after the archive metadata sync leaves the DB ahead (the new month stamped with the failed run's clock) while the live generation keeps serving the pre-run manifest; the next successful run converges both (replaces `test_archive_metadata_rolls_back_on_promotion_failure`, which pinned metadata rollback) | metadata-specific DB-ahead state (new row stamped with the failed run's clock, untouched months keep the prior timestamps) with the live manifest unchanged |
| `test_stats_contract_counts_and_keys` | 10 | stats.json key set and per-language counts (active/withdrawn/latest-limit/archive months/oldest month) | exact counts against a mixed active+withdrawn state |
| `test_stats_zero_state` | 10 | zero-state stats contract on an empty database | `oldest_archive_month_by_language` is `None`; `last_export_run_timestamp` format |
| `test_zero_state_bootstrap_layout` | 8, 10 | a zero-data first run still builds a complete (empty) generation: every configured language gets an empty index, an empty archives manifest and explicit `items/` and `archives/` directories, and the pointer is established | **unique:** per-language empty-generation layout contract consumed by the site loaders |
| `TestStreamingJsonArraySerialization::{test_empty_array,test_single_entry,test_many_entries_with_unicode_and_special_chars}` | 9 | monthly archives are serialized as a chunk stream (memory bounded by batch size, never by month size); the joined chunks equal the canonical `serialize_json_bytes` list serialization byte for byte | **unique:** direct empty / single-entry / 25-entry unicode-and-special-chars matrix against the `json.dumps(indent=2)` reference |

## 8. `tests/test_cjk_slug_fallback.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `test_cjk_titles_fall_back_and_stay_frozen` | 1 | titles that slugify to empty (CJK-only) fall back to `item`, `item-2`, ... and stay frozen across republish | end-to-end fallback for two CJK items including collision suffix |

## 9. `tests/test_config_validation.py` and `tests/test_cli_failures.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `TestPydanticValidation::*` (6 tests) | config | settings schema rejects empty `target_languages`, unknown coverage policy, non-positive `batch_size`/`latest_limit`, non-`month` granularity, missing sections | per-field error context for each rejection |
| `TestConfigFileLoader::*` (6 tests) | config | loader rejects missing file, non-mapping/empty/scalar YAML and propagates schema errors; valid config loads | loader-level surface distinct from the schema tests |
| `TestCliFailureSurface::test_validate_missing_database_fails` | — | `validate` fails structurally when the database file is missing | CLI exit-code surface |
| `TestCliFailureSurface::test_validate_missing_translation_table_fails` | — | `validate` fails when `translation_output` does not exist | structural validation order |
| `TestCliFailureSurface::test_validate_language_without_completed_translations_fails` | 7.1 | configured language with zero completed translations is a blocking validation failure | blocking vs warning distinction |
| `TestCliFailureSurface::test_run_warns_but_succeeds_with_missing_language` | 7.1 | `run` warns once and exits gracefully instead of aborting | warning-once-per-run plus success exit |
| `TestCliFailureSurface::test_rebuild_warns_but_succeeds_with_missing_language` | 7.1 | same graceful behavior for `rebuild` | rebuild variant |
| `TestCliFailureSurface::test_run_invalid_config_fails_structurally` | — | an invalid config file fails before any database or export mutation | no partial side effects |

## 10. `tests/test_author_metadata.py` and `tests/test_label_prefixes.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `TestAuthorMetadataValidatorMatrix::test_invalid_metadata_rejected_with_field_context` | payload | `author_metadata` rejects malformed JSON, non-dict payloads, missing/blank `source_module`, and human/hybrid writers without a non-empty `editor` | per-case error-token matrix |
| `TestAuthorMetadataValidatorMatrix::test_valid_writer_type_shapes_accepted` | payload | human/hybrid with editor and machine without editor all validate | positive matrix |
| `TestAuthorMetadataValidatorMatrix::test_disclosure_note_mapping` | payload | machine writer maps to the AI disclosure note; human/hybrid export no note | disclosure mapping rules |
| `TestAuthorMetadataEndToEnd::test_first_publish_leaves_no_state_for_any_invalid_case` | payload | invalid author metadata on first publish leaves neither files nor DB rows | state-cleanliness per invalid case |
| `TestAuthorMetadataEndToEnd::test_invalid_update_preserves_existing_publication` | payload | invalid metadata on an update path keeps the existing publication untouched | update-path preservation |
| `TestLabelPrefixValidation::test_label_prefix_variants_rejected_in_summary_and_bullets` | payload | UI-label prefixes (`Key Claim:` etc., all languages/casings) are rejected in `summary_short` and bullets | prefix matrix over the shared `UI_LABELS` fixture tuple |
| `TestLabelPrefixValidation::test_non_prefix_label_occurrences_accepted` | payload | label words appearing mid-sentence (not as a prefix) pass | guards against over-matching |

## 11. `tests/test_batching.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `TestCrossBatchAggregates::test_cross_batch_index_and_archive_with_batch_size_one` | 9 | index and archive pagination stay correct when every query page is one row | **unique:** batch_size=1 forces the multi-page loop for every aggregate |
| `TestCrossBatchAggregates::test_cross_batch_index_and_archive_with_batch_size_two` | 9 | same correctness across two-row pages including a partial last page | even/odd page boundaries |
| `TestIncrementalArchiveScope::test_unaffected_archive_kept_after_update_in_other_month` | 8 | an update in one month does not rewrite another month's archive (bytes and `updated_at`) | byte-level untouched-archive invariance |
| `TestIncrementalArchiveScope::test_unaffected_archive_kept_after_withdrawal_in_other_month` | 8 | same invariance for a withdrawal in another month | withdrawal variant |

## 12. `tests/test_migrations.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `TestSplitSqlStatements::*` (6 tests) | migrations | the migration SQL splitter handles comments, multi-statement files, block comments and missing trailing semicolons | splitter unit matrix |
| `TestRunMigrations::test_real_publish_migrations_are_rerun_idempotent` | migrations | applying the shipped v001+v002 migrations twice is a no-op | rerun idempotency of the real migration files |
| `TestRunMigrations::test_migrations_apply_in_sorted_order` | migrations | migration files apply in filename sort order with markers recorded | ordering plus marker rows |
| `TestRunMigrations::test_failed_migration_rolls_back_without_marker_or_partial_ddl` | migrations | a failing migration leaves neither a marker row nor partial DDL | rollback cleanliness |
| `TestRunMigrations::test_missing_migrations_directory_is_a_no_op` | migrations | a missing migrations directory does not error | no-op contract |

## 13. `tests/test_handoff_contract.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `test_documented_read_dependencies_exist_in_real_schema` | handoff | every upstream table/column publish reads (DATA_CONTRACT.md section 4) exists in the real upstream migrations | **unique:** real-migration schema, not the mock five-column schema |
| `test_publish_foreign_keys_reference_real_tables` | handoff | publish-layer foreign keys resolve against the real upstream tables | FK target verification |
| `test_minimal_publish_path_against_real_schema` | handoff | seed → run → artifacts succeed against the real upstream schema end to end | full publish path on the real schema |
| `test_source_item_delete_cascades_publish_state` | handoff | deleting a `source_item` cascades through publish-owned state as documented | cascade behavior on the real schema |

---

## 14. `tests/test_generation_pointer.py` — generation + pointer contracts

Covers the generation/pointer surface not pinned by the rewritten suites in
sections 1-13: bootstrap shape, id allocation, pointer atomicity, the
single-writer lock, the generation-phase snapshot, retention, flat-residue
bootstrap, fail-stop on corrupt live state, rebuild semantics
and hash-stream-driven archive stamping, plus the hardlink-reuse surface
(hash stream format and integrity, digest index lifecycle, legacy
transition, reuse correctness and link safety).
Convergence after failed builds/pointer switches is covered by section 2;
no-change rerun byte stability by section 4; junction safety by section 6.

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `TestBootstrapGeneration::test_first_run_with_data_establishes_pointer_and_meta` | — | the first successful run always builds a complete generation and establishes the pointer | exact pointer field values, generation id derived from the run timestamp, `meta.json` carrying the `file_hashes` reference and the hash stream covering every artifact (items included) with digests matching the on-disk bytes |
| `TestBootstrapGeneration::test_first_run_without_data_establishes_empty_generation_and_pointer` | — | zero-state bootstrap: the pointer and `meta.json` are established even though every aggregate is empty | empty-generation hash-stream path set (layout details pinned by `test_zero_state_bootstrap_layout`, section 7) |
| `TestGenerationIdAllocation::test_same_second_builds_get_suffix` | — | generation ids derive from the single run timestamp; same-second builds get a `-r2`/`-r3` collision suffix | three same-second builds produce base/`-r2`/`-r3` directories without overwriting |
| `TestPointerAtomicity::test_pointer_write_retries_sharing_violation_then_succeeds` | — | the pointer switch retries a sharing violation a limited number of times | **unique:** warning logged on the blocked attempt; the run succeeds once the blocker clears |
| `TestPointerAtomicity::test_pointer_write_failure_after_retries_keeps_old_pointer` | — | a pointer switch that exhausts its retries fails stop with the old pointer still valid | **unique:** the temp file is cleaned up, the live generation is byte-identical, and the next unblocked run converges |
| `TestSingleWriterLock::test_run_refused_while_lock_is_held_and_lock_file_persists_after_run` | — | the whole run is serialized through `publish_runner.lock` next to the database file (curate/translate precedent) | **unique:** `RuntimeError` while the lock is held; the lock file deliberately persists after a finished run (unlinking would allow an inode-reuse race) and is directly re-acquirable |
| `TestGenerationPhaseSnapshot::test_concurrent_upstream_write_during_build_is_excluded` | — | the generation phase (plan, fingerprint pass, write pass) reads one held SQLite snapshot opened with `BEGIN IMMEDIATE`, so a generation never mixes pre- and post-update DB states | **unique:** a second connection's own `BEGIN IMMEDIATE` mid-build is rejected (`SQLITE_BUSY`) before any write happens; the run completes successfully and switches the pointer |
| `TestRetention::test_retention_keeps_only_five_newest_generations` | — | `generations/` keeps the newest 5 generation directories | seven builds leave exactly the five newest, with the pointer on the latest |
| `TestRetention::test_retention_never_deletes_live_generation` | — | the generation the live pointer references is never deleted, even in pathological orderings | **unique:** the protected oldest retiree survives while an unprotected one is swept |
| `TestRetention::test_retention_deletion_failure_is_warn_only_and_converges_next_run` | — | retention deletion failures (files held by a reader) are warn-only and never fail the run; the backlog is retired on a later run | **unique:** `OSError` injection keeps the run successful; the next run retries and converges to five |
| `TestRetention::test_retention_orders_same_second_suffixes_numerically` | — | "newest" is chronological: the same-second `-rN` suffix sorts numerically, never lexicographically (`-r10` must outrank `-r2`) | **unique:** eleven same-second generations keep `-r7`…`-r11`; a plain string sort would delete `-r10` while keeping older `-r5`…`-r9` |
| `TestRetention::test_same_second_id_allocation_never_refills_retired_gaps` | — | same-second suffixes are allocated after the highest surviving suffix, never refilling gaps left by retention | **unique:** the reviewer's four-step sequence — retire base, allocate `-r7` (not base), next sweep deletes `-r2` while the fresh `-r7` survives |
| `TestFlatResidueBootstrap::test_flat_residue_without_pointer_is_ignored_and_bootstraps` | — | flat-layout residue at the export root with no pointer is inert: the run bootstraps the first complete generation from the DB without any flat-tree verification warning | **unique:** the residue stays in place untouched; the bootstrap generation is keyed by the run timestamp and contains all items |
| `TestCorruptStateFailStop::test_corrupt_pointer_variants_fail_stop` | — | a corrupt `current.json` (unparseable, missing fields, malformed or path-traversal generation id, missing generation directory, bad field types, empty `languages`, calendar-impossible timestamp) is fail-stop; only a *missing* pointer triggers bootstrap | **unique:** eleven-variant subtest matrix; nothing new is built, the live generation survives, and restoring the valid pointer reconverges |
| `TestCorruptStateFailStop::test_missing_or_corrupt_live_meta_json_fails_stop` | — | a missing or corrupt `meta.json` on the live generation is fail-stop, never a silent rebuild trigger; a reference-less `meta.json` that fails the legacy witness checks (for example an empty `aggregate_file_hashes` table) is corruption, not a legacy generation | missing, unparseable and witness-failure variants (stream-level corruption variants live in the `TestHashStreamCorruption` rows below) |
| `TestRebuildSemantics::test_rebuild_forces_new_generation_and_next_incremental_run_stays` | 5 | `rebuild` always builds a complete new generation and restamps every manifest, so the fingerprint legitimately changes; the next no-change `run` must not build again | **unique:** the rebuild summary counts the full active published set; aggregates are restamped while item payloads stay byte-identical across the rebuild |
| `TestArchiveStamping::test_unchanged_archive_keeps_db_stamp_via_meta_hash_match` | 8, 10 | with a matching live hash-stream digest the planned manifest stamp is the recorded DB value verbatim — never the run's wall clock | **unique:** a pre-existing DB stamp is carried into a new generation with a byte-identical archive payload; the state is settled afterwards |
| `TestHashStreamFormat::test_stream_covers_every_artifact_in_fixed_order_with_disk_matching_digests` | — | every generation records one hash-stream record per artifact (item payloads included) in fixed artifact order, with digests matching the actual on-disk bytes | **unique:** the exact full path sequence — languages in config order, per language `index.json` → `archives/index.json` → months ASC → item slugs ASC, `stats.json` last |
| `TestHashStreamFormat::test_stats_record_digest_matches_real_bytes_including_timestamp` | — | the stats.json stream record equals the digest of the real on-disk bytes including `last_export_run_timestamp` (the dual-digest rule); stats.json is physically written whenever its bytes differ | **unique:** the recorded digest differs from the excluded-timestamp fingerprint variant; the stats.json inode changes across builds |
| `TestDigestIndexLifecycle::test_digest_index_creates_missing_export_root_for_bootstrap` | — | the run's temporary digest index creates a missing export root (bootstrap) and is discarded at teardown | no `.digest-index*` leftover after a run |
| `TestDigestIndexLifecycle::test_digest_index_cleanup_removes_owned_sqlite_sidecars` | — | a crashed run's owned SQLite set (main file plus `-journal`/`-wal`/`-shm` sidecars) is removed when the next run creates its index | **unique:** a junk-content main file (unopenable without recovery) plus all sidecars are gone after a successful run |
| `TestHashStreamCorruption::test_referenced_stream_missing_fails_stop` | — | a referenced-but-missing hash stream is fail-stop | pointer and generation set untouched |
| `TestHashStreamCorruption::test_referenced_stream_empty_fails_stop` | — | an empty stream is corruption, not a zero-data state | pointer and generation set untouched |
| `TestHashStreamCorruption::test_malformed_stream_record_fails_stop` | — | every stream line must be a well-formed record | five-variant subtest matrix: non-JSON line, missing `path`, missing `digest`, bad digest format, non-object record |
| `TestHashStreamCorruption::test_illegal_or_duplicate_stream_path_fails_stop` | — | stream paths must be legal generation-relative paths and must not repeat | six-variant subtest matrix (`..`/`.`/empty segments, leading `/`, backslash, duplicate); duplicate detection is delegated to the prior table's PRIMARY KEY so no resident seen-set is needed |
| `TestHashStreamCorruption::test_valid_prefix_truncation_fails_stop` | — | the final stream record must be `stats.json`: even a valid-prefix truncation landing on a line boundary is corruption | dropped-final-line variant |
| `TestHashStreamCorruption::test_meta_reference_with_unexpected_value_fails_stop` | — | the `meta.json` `file_hashes` reference must be exactly `file_hashes.jsonl` | different-name, relative-path-injection and absolute-path variants |
| `TestHashStreamCorruption::test_mid_stream_digest_edit_degrades_to_physical_write` | — | the safe-degradation half of the corruption rules: a non-matching recorded digest forfeits reuse of that entry only; the run succeeds with correct output | **unique:** the tampered entry is physically rewritten with byte-identical content while an intact entry still links |
| `TestLegacyTransition::test_legacy_live_generation_no_change_run_neither_fails_nor_builds` | — | a live legacy-shape generation carries no reuse information: no-change runs against it neither fail nor spuriously build (archive stamping falls back to digest-compare) | **unique:** legacy notice logged; only the pointer's `last_successful_run_at` advances |
| `TestLegacyTransition::test_legacy_live_generation_first_content_change_writes_everything_and_establishes_stream` | — | the first content-changing build against a legacy generation physically writes every artifact and establishes the full hash stream; normal hardlink reuse applies from then on | zero links from the legacy generation; links present in the following build |
| `TestLegacyTransition::test_legacy_witness_hashes_are_never_used_for_reuse` | — | the legacy witness table is a format witness, never a hash source | **unique:** genuinely matching witness digests still produce zero links |
| `TestLegacyTransition::test_referenceless_meta_failing_witness_fails_stop` | — | a reference-less `meta.json` must positively match the four-check legacy witness (strict id, calendar-valid `created_at`, well-formed fingerprint, non-empty legal `aggregate_file_hashes`) | six-variant subtest matrix: bad generation id, calendar-invalid `created_at`, bad fingerprint, empty table, illegal path key, bad digest value |
| `TestLegacyTransition::test_null_file_hashes_reference_fails_stop` | — | a present-but-null `file_hashes` field is corruption, never legacy: a genuine legacy `meta.json` never carries the field at all, so a null marks a damaged newer file even beside a valid-looking aggregate table | two-variant subtest matrix: null beside a valid aggregate table, null reference alone |
| `TestHardlinkReuse::test_unchanged_artifacts_are_hardlinked_and_changed_ones_rewritten` | — | unchanged artifacts are `os.link()`-ed from the trusted prior generation; changed ones are physically written | per-artifact `(st_dev, st_ino)` equality/inequality on NTFS (Linux verified on the target VPS before rollout) |
| `TestHardlinkReuse::test_reused_items_skip_second_db_read_and_serialization` | — | digest carry-over: the full item stream runs exactly once per run (the fingerprint pass); the write pass re-reads only changed items, one by-slug fetch each | **unique:** spies on `_iter_item_payloads` and `fetch_published_payload_by_slug` |
| `TestHardlinkReuse::test_reuse_works_with_item_count_several_times_batch_size` | 9 | hardlink reuse stays correct across batch boundaries (23 items with `batch_size=5`) | per-artifact inode assertions over 46 item files |
| `TestLinkSafety::test_link_failure_falls_back_to_physical_write` | — | any `os.link` failure (simulated EXDEV; no real cross-device setup exists on the development machine) falls back to a safe physical write without affecting generation correctness | patched `os.link`; byte-correct output with no shared inodes |
| `TestLinkSafety::test_post_link_verification_mismatch_removes_destination_and_fails_stop` | — | a link whose destination does not resolve to the source's `(st_dev, st_ino)` removes the destination and fails stop | **unique:** `os.link` patched to copy instead of link; pointer and live generation untouched; staging and digest index cleaned by teardown |
| `TestLinkSafety::test_non_regular_link_source_falls_back_to_physical_write` | — | a symlink/reparse link source is never linked | file-symlink injection carrying smuggled bytes; output carries the planned bytes (skipped when the platform cannot create one) |
| `TestLinkSafety::test_nested_reparse_link_source_falls_back_to_physical_write` | — | a regular artifact reached through a junction/symlink parent fails source-containment validation and is never linked | **unique:** junction to an outside directory with smuggled bytes; output carries the planned bytes and never shares the outside inode (skipped when the platform cannot create one) |
| `TestRebuildAndRetentionUnderReuse::test_rebuild_physically_rewrites_even_when_hashes_match` | 5 | `rebuild` forces a full physical rewrite even when hashes match | byte-identical item payloads with every inode distinct |
| `TestRebuildAndRetentionUnderReuse::test_linked_files_are_never_modified_in_place` | — | generation contents are never modified in place, including files hardlinked into later generations (safety-critical shared-inode immutability) | full byte+inode snapshot of the first generation across two subsequent builds |
| `TestRebuildAndRetentionUnderReuse::test_retention_unlinks_shared_inodes_without_breaking_retained_generations` | — | retention unlinks retired generations that share inodes with retained generations without breaking them | **unique:** link-count assertion where the filesystem reports it (`st_nlink` equals the surviving generation count) |

---

## 15. Former gaps now covered

The gaps listed in `PUBLISH_TEST_MAINTAINABILITY_PLAN.md` section 3 are all
covered as follows:

- Section 3.4 unchanged-rerun idempotency → section 4.
- Section 3.5 index/archive ordering and tiebreakers → section 5.
- Section 3.6 coverage-loss withdrawal and language-set shrink/expand → section 6.
- Section 3.7 config validation and CLI failure surface → section 9.
- Section 3.8 author metadata invalid-input matrix → section 10.
- Section 3.9 manifest/stats aggregate contract incl. manifest `updated_at` → section 7.
- Section 3.10 cross-batch correctness and untouched-archive invariance → section 11.
- Section 3.12 CJK-title slug fallback → section 8.
- Phase 4 migration runner and real-migration handoff contract → sections 12-13.

