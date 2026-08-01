# Translate Test Suite — Baseline, Coverage Map, Confirmed Conclusions

**Status:** Active test-support record (Phase 0 output of
[TRANSLATE_TEST_MAINTAINABILITY_PLAN.md](../../../known_issues/TRANSLATE_TEST_MAINTAINABILITY_PLAN.md);
updated through Phase 4)
**Recorded:** 2026-08-01 (Phase 0 baseline); **Updated:** 2026-08-01 (Phase 4)

---

## 1. Baseline

- Command: `python -m pytest modules/translate/tests -q`
- Phase 0 result: **48 passed** (9 scenario tests in `test_translate.py`, 39
  contract tests in `test_five_field_contract.py`).
- Phase 0 stability spot-check: same command run 5 times consecutively on
  2026-08-01, all 5 runs passed (8.3s–10.4s), no flaky failure observed.
- `python -m modules.translate.src.cli validate` validates the active config
  successfully.
- Active config facts: provider `mini-proxy` with
  `supports_structured_output: false` (so the `json_object` fallback request
  path is exercised at runtime); execution policy `batch_size: 200`,
  `max_concurrent_requests: 20`, `rate_limit_per_minute: 1200`,
  `retry_attempts: 3`; `validation.content_ratio_limit: 5.0`.

**Current numbers after the 2026-08-01 review follow-ups (P1 + P2):**

- `python -m pytest modules/translate/tests -q`:
  **149 passed, 1 skipped, 53 subtests passed** (~8.2s), 5 consecutive runs
  stable.
- Review P1 regressions added: a permanent (non-429 4xx) failure locks the
  task out of the automatic queue immediately, with operator `--force` as the
  escape hatch (test_state.py); non-positive `--batch-size` is rejected at CLI
  option parsing and at the orchestrator boundary (test_cli.py, test_state.py);
  non-positive `execution_policy.batch_size` fails config validation
  (test_config.py).
- Review P2 regressions added: non-integer `batch_size` values (floats,
  numeric strings, bools) from a direct Python caller are rejected at the
  orchestrator boundary before any lock file, DB write or API traffic
  (test_state.py); an upstream content-fingerprint change releases a
  permanently locked failed row via stale detection, so the next bulk run
  retries the new content without `--force` (test_state.py).
- The 1 skipped test is the intentionally pending edit-originated handoff
  contract (`test_handoff_contract.py::TestEditOriginatedHandoffPending`),
  skipped until the edit module lands (plan Phase 4 item 2).
- Previous baselines (2026-08-01): 147 passed, 1 skipped, 49 subtests passed
  (post-P1 batch); 142 passed, 1 skipped, 43 subtests passed (post-Phase 4).

This baseline is not a CI gate; it is the reference for judging whether each
plan phase introduces regressions.

## 2. Coverage Map (post-Phase 4 layout)

Per-file responsibilities after the Phase 3 additions and the Phase 4
convergence. Categories: **five-field** (fingerprint / response schema
contract), **handoff** (upstream assembler), **validation** (runner-side
quality rules), **state** (status transitions, stale, retry, failure safety),
**CLI**, **execution** (API call paths), **database** (migration / transaction
/ DDL / queue utilities).

| File | Tests | Responsibility |
| :--- | :--- | :--- |
| test_five_field_contract.py | 44 | Precise low-level five-field contract tests: fingerprint golden vectors (TestContentFingerprint), assembler passthrough (TestHandoffAssemblerFiveField), upstream bullet shape enforcement 8-combination matrix (TestUpstreamBulletShapeEnforcement), response schema validation (TestResponseSchemaValidation), quality rules — title caps, aggregate ratio, zh/ja script presence incl. exact failure messages, label guard (TestTranslationQualityValidation), bypass direction and bypass stale rules (TestBypassAndStale), first-failure and forced-rerun failure safety (TestFailureSafety). |
| test_translate.py | 3 | Public journey tests through orchestrate_run() + the assembler (TestTranslateJourneys): assemble → preview → run (preview never calls the API; API-written rows carry the provider model, pinning the non-bypass direction), upstream edit → re-assemble → stale detection retranslates in the same run, force rerun failure preserves the completed row then success overwrites atomically with translated_at refreshed. |
| test_handoff_contract.py | 8 + 1 skipped | Consumer handoff contract pinned to the REAL upstream migrations (ingest → curate → translate applied via run_migrations): five-field/fingerprint/language/approved_at/freshness-marker materialization (TestHandoffMaterialization), illegal bullet shape rejected under the real schema, delta pre-screen incl. metadata-only refresh (TestDeltaPrescreen), source_item FK cascade into approved_content_record and translation_output (TestHandoffForeignKeys), repository read + queue read contract (TestQueueReadContract), and the skipped pending edit-originated handoff contract (TestEditOriginatedHandoffPending). |
| test_config.py | 5 | Locked 5.0 ratio policy and YAML-derived limits flowing into fetch_llm_translation (Phase 1); non-positive `execution_policy.batch_size` fails config validation (review P1). |
| test_execution.py | 26 | LLM execution policy: general 4xx single-attempt failures, dry-run process lock, request payload construction, response parsing, retry eligibility matrix, rate-limit stagger, semaphore concurrency cap, cross-process lock (Phase 2). |
| test_database.py | 27 | split_sql_statements, migration idempotency and failure rollback, transaction() semantics, translate DDL constraints (UNIQUE/CHECK/FK cascade/index semantics), upsert, detect_and_mark_stale (6 tests incl. bypass exemption), pending task selection (Phase 3). |
| test_state.py | 22 | Public orchestrate_run state machine: bulk queue eligibility matrix, retry_count progression to logical lock, permanent 4xx immediate lock + operator force escape hatch (review P1), upstream fingerprint change releasing the permanent lock via stale (review P2), operator force on locked failure, fresh completed forced rerun (atomic overwrite / failure preservation), stale row forced rerun, dry-run, preview, batch source-item semantics incl. non-positive and non-integer batch-size rejection (review P1/P2), run summary counts (Phase 3). |
| test_cli.py | 14 | CLI surface with patched load_dotenv and temporary config/workspace: validate, run preview/dry-run/summary/exit codes/batch-size (incl. non-positive usage error, review P1)/force, status, assemble rejection reporting (Phase 3). |
| support.py | — | Shared test-support module: temporary workspace/DB builders, minimal upstream fixture, config builders, response factories, seed helpers, row snapshots, FakeLLMClient (Phase 0; reused everywhere since Phase 3/4). |

No test is deleted under this plan unless a replacement covers the same rule;
Phase 4 removed node IDs and their replacements are listed in section 5.

## 3. Confirmed Conclusions (decided, no longer open)

1. **Content ratio limit is 5.0.** Active config was deliberately relaxed from
   1.2 to 5.0 in commit `bc165eb` (2026-06-23). The outdated values are the
   `TRANSLATION_POLICY.md` / `EXECUTION_POLICY.md` prose, the
   `ValidationConfig` Pydantic default, and handwritten 1.2 mock fixtures —
   these are doc/default/fixture sync work (Phase 1), not a policy reopen.
2. **Upstream freshness marker.** `author_metadata.upstream_updated_at` is the
   correct delta pre-screen marker. The handoff row's own `updated_at` is a
   system materialization timestamp refreshed by every assembly run and must
   never be compared against upstream timestamps. `DATA_CONTRACT.md` §1.1,
   §1.5, §2.1.2 are synced in Phase 1.
3. **Non-persisted forced rerun.** `--force` on a `completed` row whose source
   fingerprint and config still match is an in-memory execution mode only: the
   row is never rewritten to `pending` before the API call. Success atomically
   overwrites the five content fields in one short transaction; API /
   validation / interruption failure leaves the completed row untouched (no DB
   write, no retry_count increment). A `stale` row is not protected by this
   model: even with `--force` it follows the normal stale retry path.
   `STATE_TRANSITIONS.md` has been aligned with this model.
4. **Deterministic batch order (target).** `batch_size` counts source items.
   Selection order is `approved_at ASC, parent_content_id ASC`
   (parent_content_id is the deterministic tie-breaker for equal timestamps),
   then every eligible target-language task of each selected article is
   expanded. The batch boundary must not split one article's pending language
   set. Runtime alignment lands in Phase 3 with its regression tests.

## 4. Fixture Contract

- `support.py` is the shared test-support module (temporary workspace/DB
  builders, response factories, active-config loader, seed helpers, row
  snapshots). Key preconditions — bullet shapes, downstream_action, status,
  retry_count — are explicit arguments.
- The minimal upstream tables built by `create_minimal_upstream_tables()` are
  an **isolated-test-only fixture**. They carry only the columns the handoff
  assembler queries and are not a representation of canonical
  ingest/classify/curate schemas (plan section 3.6). The consumer handoff
  contract tests in `test_handoff_contract.py` deliberately do NOT use this
  fixture: they build temporary DBs from the real ingest/curate/translate
  migration files (plan Phase 4 item 1).
- Tests must not read the workspace `data/canonical.db`, must not read
  `.env`, and must patch `load_dotenv()` in CLI tests (plan section 7 item 5).
  Tests must never issue real HTTP requests.
- Phase 4 helper deduplication: the duplicated per-file schema setup
  (`create_mock_upstream_tables`, `create_five_field_tables`), the per-file
  seed helpers (`seed_curation_approval`, `seed_approved_record`,
  `seed_translation_row`/`seed_translation_output`), the handwritten mock
  config builders and the local `five_field_response()` factories were
  replaced by the shared `support.py` equivalents
  (`support.build_test_db` / `build_temp_workspace`,
  `support.seed_curation_approval` / `seed_approved_record` /
  `seed_translation_row`, `support.build_mock_config` with
  `support.make_target_language`, `support.make_five_field_response`).
  Call sites that relied on factory defaults now pass the exact former
  default values explicitly (module-level `SAMPLE_*` constants in
  test_five_field_contract.py), so effective test data never changed.

## 5. Phase 4 Removals (removed node IDs → replacement coverage)

All 9 legacy `test_translate.py::TestTranslateModule` scenario tests were
removed in Phase 4. Each has equal or more precise replacement coverage, and
every replacement is green in the post-Phase 4 suite:

- `test_translate.py::TestTranslateModule::test_handoff_assembler_passthrough_and_fingerprint`
  → `test_handoff_contract.py::TestHandoffMaterialization` + `TestDeltaPrescreen`
  (now pinned to the real upstream schema).
- `::test_validation_rules`
  → `test_five_field_contract.py::TestTranslationQualityValidation`
  (title cap, aggregate ratio).
- `::test_cache_staleness_and_invalidation`
  → `test_database.py::TestDetectAndMarkStale` (6 tests).
- `::test_translation_success_and_validation_errors`
  → `TestBypassAndStale` + `TestFailureSafety` (contract level) and
  `test_state.py::TestRetryCountProgression` + `TestFreshCompletedForcedRerun`
  (public path).
- `::test_delta_prescreen_with_upstream_timestamps`
  → `test_handoff_contract.py::TestDeltaPrescreen`.
- `::test_distinguish_stale_failure_vs_forced_failure`
  → `test_state.py::TestStaleRowForcedRerun` + `TestFreshCompletedForcedRerun`
  (public path, replaces manual task dicts).
- `::test_cli_commands_verification`
  → `test_cli.py` (validate/assemble/status/preview with patched load_dotenv;
  the legacy test read the workspace `.env` and workspace config dir).
- `::test_cjk_script_validation`
  → `TestTranslationQualityValidation` zh/ja script tests (incl. mixed-script
  tolerance) + new exact-message assertions (`lacks CJK Unified Ideographs`,
  `lacks Hiragana/Katakana`).
- `::test_bypass_policy_under_new_mother_draft_language`
  → `TestBypassAndStale` (bypass direction) + `test_translate.py` journey 1
  asserting provider `model_name` on API-written rows (non-bypass direction).

Also removed with the legacy file: all local helpers it carried
(`create_mock_upstream_tables`, `five_field_response`,
`seed_curation_approval`, `seed_approved_record`, `make_task`,
`seed_translation_output`, `_run_no_wait`) — replaced by `support.py`
equivalents or by the journey tests' own deterministic driver.
