# Publish Test Coverage Map

**Document version:** v1.1
**Updated:** 2026-08-01
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
`make_config()`, `seed_item()`, artifact readers and `FakeClock`. It carries no
assertions of its own; the real-migration schema contract lives in
`test_handoff_contract.py` (section 13).

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
| `test_first_time_file_write_compensation` | — | first-publish file write failure leaves no publish DB state | **unique:** first-time publish compensation deletes the new `publish_record` instead of marking withdrawn |
| `test_warning_per_command_scope` | — | missing target language warning emitted once per run | **unique:** warning count per command execution, across two consecutive runs |
| `test_update_file_write_compensation` | — | update-path file write failure restores previous DB state | **unique:** prior fingerprint/status restored after failed update write |
| `test_direct_rebuild_after_upstream_withdrawal` | 5, 8 | direct `rebuild` (no preceding incremental run) synchronizes withdrawal | rebuild-only withdrawal path |
| `test_rebuild_file_write_failure_divergence_prevention` | — | rebuild file write failure does not clear or corrupt the export directory | **unique:** pre-existing export files survive a failed rebuild |
| `test_archive_index_batching_limit` | 9 (partial) | `index.json` truncated at `latest_limit` | only latest-limit truncation; does **not** cross a batch boundary (covered by section 11) |
| `test_promotion_midway_failure_reversion` | — | promotion failure reverts export tree and database | **unique:** full byte-identical export-tree snapshot restore plus DB fingerprint/`updated_at` restore plus no new item rows |

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
| `test_unchanged_rerun_preserves_state_and_bytes` | 6, 7 | a second unchanged incremental run publishes/withdraws nothing, keeps every artifact byte-identical and every DB row unchanged | full export-tree byte snapshot plus publish-table snapshot across reruns; `published_at`/`updated_at` stability via `FakeClock` |

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
| `TestConfiguredLanguageSetChanges::test_shrink_withdraws_only_removed_language` | 3, 8 | language-set shrink withdraws only the removed language; remaining languages keep artifacts, publish timestamps and archive metadata | **unique:** removed language's index/archives/manifest and `publish_archive_metadata` rows are gone, not just its item files |
| `TestConfiguredLanguageSetChanges::test_shrink_rebuild_removes_removed_language_artifacts` | 5, 8 | a full rebuild under the shrunk set reconciles the removed language the same way | rebuild-mode removal of the removed language's whole artifact tree; remaining metadata refreshed with the rebuild clock |
| `TestConfiguredLanguageSetChanges::test_orphan_directory_without_publish_state_is_preserved` | 8 | directory names and generic subdirectory shapes (`items/`, `archives/`) are not ownership evidence: without `publish_language_status` rows a directory is never touched (replaces `test_orphan_language_directory_without_db_rows_removed`, which pinned the abandoned structural heuristic) | **unique:** `assets/items/customer.json` survives an empty run |
| `TestConfiguredLanguageSetChanges::test_unrelated_directory_is_left_alone` | 8 | a top-level directory with neither publish-owned rows nor the publish directory structure is not publish's to clean | **unique:** `assets/index.json` and non-pattern files survive an empty run |
| `TestConfiguredLanguageSetChanges::test_removed_language_junction_is_not_followed` | 8 | the cleanup never deletes through a removed-language directory that is a junction/symlink: the link target's files are untouched, the sweep and the withdrawn-item cleanup both skip with a warning, and the DB side still reconciles | **unique:** real junction (`mklink /J`) to a target outside the export tree |
| `TestConfiguredLanguageSetChanges::test_shrink_allows_missing_removed_language_directory` | 3, 8 | shrink completes its database reconciliation when an operator has already removed the obsolete language directory | **unique:** absent path does not make the reparse-point guard raise `FileNotFoundError` |
| `TestConfiguredLanguageSetChanges::test_removed_language_nested_junction_is_not_followed` | 8 | the cleanup never follows a junction/symlink in the `items` subdirectory of an otherwise regular removed-language directory | **unique:** both withdrawn-item cleanup and the sweep preserve the linked target's item JSON |
| `TestConfiguredLanguageSetChanges::test_removed_language_lone_index_cleaned_via_publish_state` | 8 | `publish_language_status` rows are the durable ownership evidence: a removed language's lone leftover `index.json` (no subdirectories) is still reconciled | **unique:** state-evidence path independent of directory structure |
| `TestConfiguredLanguageSetChanges::test_shrink_rolls_back_on_promotion_failure` | — | a promotion failure mid-shrink restores the removed language's files and metadata rows and rolls its publish status back to published | **unique:** failure injected on the second removed-language artifact removal |
| `TestConfiguredLanguageSetChanges::test_expand_with_incomplete_new_language_withdraws_item` | 3 | expanding the set under strict_match withdraws items lacking the new language until coverage completes | expand-driven withdrawal of all configured languages |
| `TestConfiguredLanguageSetChanges::test_expand_with_complete_new_language_adds_only_that_language` | 3 | expanding with complete coverage adds only the new language's artifact | existing languages keep artifacts and publish timestamps; `published_count == 1` |

## 7. `tests/test_aggregate_contracts.py`

| Node ID | Focus | Protected rule | Unique assertions |
| :--- | :--- | :--- | :--- |
| `test_manifest_contract_fields_and_ordering` | 8, 10 | manifest entries carry `archive_month`/`file_name`/`item_count`/`updated_at`, order DESC by month, and every entry has a real file on disk | exact field-level manifest contract for both languages |
| `test_manifest_updated_at_lifecycle` | 8, 10 | `updated_at` moves only when its archive is (re)written: create, withdrawal-rewrite, empty-delete, recreate, rebuild | full timestamp lifecycle across five runs via `FakeClock`; metadata row deleted with empty archive |
| `test_missing_metadata_heals_by_rewriting_archive_once` | 8, 10 | pre-v002 databases (archives without metadata rows) are healed by rewriting the archive once, never by stamping metadata without a write; steady-state runs do not rewrite | **unique:** mtime-backdated archives prove the one-time rewrite with identical bytes, then prove no further rewrite |
| `test_archive_metadata_rolls_back_on_promotion_failure` | — | a promotion-phase failure restores `publish_archive_metadata` to its pre-run values | metadata-specific rollback (no new row, prior timestamps kept) |
| `test_stats_contract_counts_and_keys` | 10 | stats.json key set and per-language counts (active/withdrawn/latest-limit/archive months/oldest month) | exact counts against a mixed active+withdrawn state |
| `test_stats_zero_state` | 10 | zero-state stats contract on an empty database | `oldest_archive_month_by_language` is `None`; `last_export_run_timestamp` format |

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

## 14. Former gaps now covered

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
