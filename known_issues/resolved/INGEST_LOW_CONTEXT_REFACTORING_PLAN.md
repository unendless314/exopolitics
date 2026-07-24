# Ingest Low-Context Queue Refactoring Plan

**Status:** Approved implementation plan, pending implementation  
**Decision:** Adopt option five, "minimal gate removal", from [INGEST_LOW_CONTEXT_INVESTIGATION.md](INGEST_LOW_CONTEXT_INVESTIGATION.md) §4  
**Target environment:** Development only  
**Database policy:** Discard and rebuild `data/canonical.db` after the refactor. No historical-item backfill, cutoff, or migration is required.

---

## 1. Objective

Remove the hard `low_context` gate from the classify pending queue while preserving module boundaries:

- `ingest` continues to determine and persist text-processing outcomes. It does not make classification or editorial-routing decisions.
- `classify` accepts every ingested item with classifiable input, including normal `low_context` outcomes.
- `curate` remains the sole owner of `publish_link`, `publish_summary`, and all other `downstream_action` decisions.

The resulting classify pending predicate is:

```sql
t.text_processing_status != 'failed'
AND (
    t.text_processing_reason IS NULL
    OR t.text_processing_reason != 'post_cleanup_empty'
)
```

This admits `completed` items and low-context items whose reason is `mostly_links`, `too_short`, `title_only`, `title_heavy`, `template_heavy`, or `truncated_to_low_context`. It excludes all `failed` items and items with no usable cleaned text (`post_cleanup_empty`).

`text_processing_status` is `NOT NULL` in the ingest schema. The explicit `IS NULL` branch is required because completed items normally have a `NULL` reason and SQL comparison with `NULL` otherwise evaluates to unknown.

---

## 2. Scope and Non-Goals

### In scope

1. Correct the classify pending-query predicate.
2. Align all contracts and module documentation that currently declare low-context items terminal before classify.
3. Update read-only analysis and dashboard report contracts so newly valid low-context classifications are neither hidden nor reported as anomalies.
4. Update repository and orchestration tests to verify the new queue behavior and analytics semantics.
5. Rebuild the disposable development database after code and tests are complete.
6. Observe the first one to two weeks of newly ingested data through corrected analysis outputs and manual curation-output samples.

### Explicitly out of scope

- No ingest sanitizer, ingestion schema, or ingest migration change.
- No classify prompt-template or classify-orchestrator prompt-input change.
- No propagation of status or reason into classify prompts.
- No curate code, query, prompt, policy, or validation change.
- No forced `publish_link` rule for low-context items.
- No historical database migration, backlog processing, cutoff, or backfill.
- No source-specific sanitizer exception.

---

## 3. Documentation Changes

Update documentation before or together with code so the contracts authorize the implementation.

### 3.1 Top-level technical documents

| File | Section | Required change |
|---|---|---|
| `docs/SYSTEM_OVERVIEW.md` | §6.2 `classify` | Replace completed-only queue wording with the exclusion of `failed` and `post_cleanup_empty` outcomes. State that low-context items enter classify. |
| `docs/MODULE_BOUNDARIES.md` | §3.2 `classify` | Permit classify to read `text_processing_status` and `text_processing_reason` only for pending-queue selection. Replace the non-completed exclusion with the two explicit exclusions. Preserve the rule that classify does not own editorial action selection. |
| `docs/DATA_LIFECYCLE.md` | §3 stage flow, §6.3 failure semantics, §12 locked decisions | Change the flow so low-context items continue to classification. Keep `failed` and `post_cleanup_empty` terminal before classify. Remove statements that low-context records cannot produce classification rows. |
| `docs/CANONICAL_ENTITY_CONTRACT.md` | §4.6 Classification Result and §5 representation boundaries | Allow `Classification Result` records for low-context items that complete classify. Retain exclusion for failed and post-cleanup-empty items, and retain ingest ownership of the observed status and reason. |

### 3.2 Module technical documents

| Module | File | Section | Required change |
|---|---|---|---|
| `ingest` | `modules/ingest/docs/SANITIZATION_STRATEGY.md` | §8.3 outcome definitions and §8.4 Scope Boundary | Redefine low-context as a sparse-text quality observation rather than classify ineligibility. Split the current combined terminal-queue statement so failed and post-cleanup-empty exclusion remains explicit. Keep the status/reason values and the rule that ingest makes no classification decision. |
| `ingest` | `modules/ingest/docs/DATA_CONTRACT.md` | §7 Decisions Locked By This Rewrite | Replace the statement that downstream classify excludes all non-completed items. Preserve ingest ownership of the status/reason fields and describe their queue-selection-only downstream use. |
| `classify` | `modules/classify/docs/README.md` | §1 architecture boundary, §2 responsibilities, §3 document map | Describe the new pending predicate and say low-context items are classified without status/reason prompt metadata. Update the document-map description from “low-context exclusion” to the new policy. |
| `classify` | `modules/classify/docs/DATA_CONTRACT.md` | Purpose and pending-query contract | Update the completed-only `classification_result` scope and replace the SQL predicate with the NULL-safe exclusion predicate. |
| `classify` | `modules/classify/docs/CLASSIFICATION_POLICY.md` | §3 Low-Context Exclusion Policy | Replace the exclusion policy with a policy that admits low-context items, excludes only failures and post-cleanup-empty text, and retains normal LLM classification for every selected item. |
| `classify` | `modules/classify/docs/IMPLEMENTATION_PLAN.md` | §3.3, Epic 4, test tasks | Replace completed-only filtering references and revise the test requirement to cover included and excluded reason codes. |
| `classify` | `modules/classify/docs/EXECUTION_POLICY.md` | Queue Selection | Replace the completed-only queue rule with the new predicate. Keep existing batching, rate-limit, and single-worker rules. |
| `analysis` | `modules/analysis/docs/METRICS_CATALOG.md` | §2.2, §2.2.1, §2.2.2, §2.3, §3.1.1 | Replace “bypass” semantics with low-context quality-observation semantics. Define pending, classified, workload, and downstream metrics using the option-five eligible population. |
| `analysis` | `modules/analysis/docs/REPORT_CONTRACTS.md` | Sources and funnel report schemas; ingest diagnostics compatibility note | Version the affected report schemas and replace `low_context_bypass_*` fields with defined non-terminal observation fields. Define all downstream funnel stages over the eligible population. Record `ingest_diagnostics` v2 as a non-dashboard diagnostic report contract. |
| `dashboard` | `modules/dashboard/docs/DASHBOARD_DESIGN.md` | §4.1 funnel and §4.2 sources | Remove “Low-Context Bypass” as a pipeline stage and rename the source KPI to the defined low-context observation metric. |
| `dashboard` | `modules/dashboard/docs/DATA_CONTRACT.md` | §4 supported schema versions and §9 compatibility expectations | Update the documented dashboard-consumed schema versions to sources `2.0.0` and funnel `3.0.0`. |

### 3.3 Documents that must not change

| Module | Files | Reason |
|---|---|---|
| `curate` | All files under `modules/curate/docs/` | Curate already owns downstream-action selection and has its own policy for link versus summary output. It must not receive an ingest-derived routing constraint in this refactor. |
| `ingest` | All remaining files under `modules/ingest/docs/` | The storage schema, source configuration, and implementation plan require no behavior or schema change. |
| Root | Any document not listed in §3.1 | No other top-level contract currently encodes the low-context classify gate. |

---

## 4. Runtime Code Changes

### 4.1 Pipeline runtime change

**File:** `modules/classify/src/database.py`  
**Symbol:** `ClassificationResultRepository.get_pending_items`

Replace:

```sql
AND t.text_processing_status = 'completed'
```

With:

```sql
AND t.text_processing_status != 'failed'
AND (
    t.text_processing_reason IS NULL
    OR t.text_processing_reason != 'post_cleanup_empty'
)
```

Keep unchanged:

- the `source_item` and `source_item_text` joins;
- `s.ingest_status = 'ingested'`;
- the unclassified condition, `c.classification_result_id IS NULL`;
- the selected columns: `source_item_id`, `title`, and `sanitized_text`;
- batch-limit behavior.

The unchanged selected columns deliberately ensure that ingest status/reason cannot be injected into the classify prompt.

### 4.2 Read-only analysis and report-contract changes

The option-five core pipeline change remains one classify query predicate. The following analysis changes are required because existing reporting assumes all non-completed records bypass classify. Without them, valid low-context classification rows become false `ORPHANED_CLASSIFICATION` anomalies and disappear from classified, curate, approval, translation, and publication metrics.

| File | Required change |
|---|---|
| `modules/analysis/src/queries/aggregation_queries.py` | Replace completed-only funnel and readiness filters with the option-five eligible predicate. Emit `low_context_observation_count` in place of old bypass fields. Treat `failed` and `post_cleanup_empty` as terminal. Update `ORPHANED_CLASSIFICATION` to flag only missing text, failed text, or post-cleanup-empty text. |
| `modules/analysis/src/queries/classify_queries.py` | Calculate the classification character-volume proxy over the eligible population, not completed-only records. |
| `modules/analysis/src/queries/ingest_queries.py` | Rename the low-context bypass query and return `low_context_observation_rate`, a quality-observation rate rather than a claimed bypass rate. |
| `modules/analysis/src/services/funnel_calculator.py` | Emit the versioned `low_context_observation_count`; remove it as a terminal funnel stage; render the corrected diagnostic label in Markdown output. |
| `modules/analysis/src/services/source_service.py` | Emit and render `low_context_observation_rate`. |
| `modules/analysis/src/services/ingest_service.py` | Emit and render `low_context_observation_rate`, then bump the non-dashboard `ingest_diagnostics` report to `2.0.0`. |
| `modules/dashboard/src/loaders/report_loader.py` | Accept the versioned analysis report fields and remove the old bypass-only model names. |
| `modules/dashboard/src/components/funnel_view.py` | Remove “Low-Context Bypass” as a funnel stage. |
| `modules/dashboard/src/components/sources_view.py` | Render the renamed low-context quality-observation KPI. |
| `modules/dashboard/config/dashboard_settings.yaml` | Bump `supported_schema_versions` in the same atomic change: `funnel` to `3.0.0` and `sources` to `2.0.0`. The dashboard loader validates each report's `schema_version` against these declared versions and flags incompatible reports, so this file must move together with the analysis service changes. |

Use these exact replacement fields and definitions:

| Previous field | Replacement field | Report(s) | Definition |
|---|---|---|---|
| `low_context_bypass_count` | `low_context_observation_count` | Funnel raw and matured metrics | Count of cohort records where `text_processing_status = 'low_context'`. It is a source-text quality observation, not a terminal or subtractive funnel stage. |
| `low_context_bypass` | `low_context_observation_count` | Funnel classification-readiness breakdown | The same low-context observation count, reported independently from eligible, classified, and pending counts. |
| `low_context_bypass_rate` | `low_context_observation_rate` | Sources and ingest diagnostics | Low-context observation count divided by total ingested items in the report cohort. |

The analysis report schema is a breaking internal contract. Increment funnel `2.0.0` to `3.0.0`, sources `1.0.0` to `2.0.0`, and the non-dashboard `ingest_diagnostics` output `1.0.0` to `2.0.0`. Update `modules/analysis/docs/REPORT_CONTRACTS.md`, analysis tests, and dashboard schema/configuration/tests atomically. `ingest_diagnostics` is not loaded by dashboard, so it is deliberately absent from `dashboard_settings.yaml`. Do not retain the old `low_context_bypass_*` names with changed semantics.

For the post-rollout cohort in §7, add read-only analysis output or a version-controlled analysis query for items meeting:

```sql
sit.text_processing_status = 'low_context'
AND (
    sit.text_processing_reason IS NULL
    OR sit.text_processing_reason != 'post_cleanup_empty'
)
AND cr.classification_result_id IS NOT NULL
```

It must report topic-class distribution, curation approval rate, and `downstream_action` distribution for that cohort. It must not write canonical data or influence curation.

### 4.3 Production code that must remain unchanged

| File | Why it remains unchanged |
|---|---|
| `modules/classify/src/orchestrator.py` | The current prompt receives only title and sanitized text. Do not pass text-processing status or reason to it. |
| `modules/classify/config/prompt_templates.yaml` | No prompt wording or inputs change under option five. |
| `modules/ingest/src/sanitizer.py` | Existing status and reason production remains the observation source. |
| `modules/curate/src/database.py` | Curate must not read status/reason for this change. |
| `modules/curate/src/orchestrator.py` | Do not add a low-context `publish_summary` rejection rule. |
| All migrations | The required columns and enum values already exist; this is a query-contract change, not a schema change. |

---

## 5. Test Changes

**File:** `modules/classify/tests/test_classify.py`

### 5.1 Repository pending-query coverage

Extend or replace `TestDatabaseRepository.test_pending_query_and_upsert` using the existing temporary SQLite fixture and `seed_test_item` helper.

Seed unclassified ingested items covering:

| Case | Status | Reason | Expected pending result |
|---|---|---|---|
| Normal sanitized item | `completed` | `NULL` | Included |
| Google-News-style link wrapper | `low_context` | `mostly_links` | Included |
| Short text | `low_context` | `too_short` | Included |
| Title-derived text | `low_context` | `title_heavy` and `title_only` | Included |
| Boilerplate/truncation text | `low_context` | `template_heavy` and `truncated_to_low_context` | Included |
| Empty after cleanup | `low_context` | `post_cleanup_empty` | Excluded |
| Text-processing failure | `failed` | `missing_body` or `sanitizer_exception` | Excluded |

Also retain the assertion that an item leaves the pending queue once `classification_result` exists.

### 5.2 Orchestration and preview coverage

Update tests whose names or assertions currently expect low-context bypass:

- `TestOrchestrator.test_orchestrate_success_and_bypass`
- `TestOrchestrator.test_orchestrate_preview_prompts_summary`

They must verify that an allowed low-context item is queried or previewed and proceeds through the existing LLM path. Add a `post_cleanup_empty` or `failed` item to each relevant test to verify that the two excluded outcomes still do not invoke the model.

### 5.3 Analysis and dashboard coverage

Update these existing tests and fixtures:

| File | Required coverage |
|---|---|
| `modules/analysis/tests/test_funnel_calculator.py` | Seed a classified low-context item and verify it is counted in classified and later eligible stages, is not emitted as a false anomaly, and is absent only when failed or post-cleanup-empty. |
| `modules/analysis/tests/test_source_classifier.py` | Verify low-context source metrics use the renamed quality-observation rate and do not describe it as a bypass. |
| `modules/analysis/tests/test_ingest_service.py` | Verify `ingest_diagnostics` v2, the renamed quality-observation metric, and the corrected report label. |
| `modules/analysis/tests/test_schema_validation.py` | Validate the incremented report schema and the replacement fields. |
| `modules/dashboard/tests/test_report_loader.py` | Verify the dashboard accepts the new analysis report schema and fields. |

### 5.4 Test commands

Run the focused suite first:

```text
python -m unittest modules.classify.tests.test_classify
```

Use the system Python interpreter in this development environment. The repository `.venv` lacks `httpx`, which `modules.classify.tests.test_classify` imports. Do not silently change the virtual environment as part of this refactor.

Run the relevant analysis and dashboard suites independently with the `.venv` pytest, the established analysis/dashboard environment (`.venv/Scripts/python.exe -m pytest ...`). Both suites also pass under the system Python (pytest 9.1.1), but prefer the `.venv` runner so interpreter choice stays consistent across modules:

```text
.\.venv\Scripts\python.exe -m pytest modules/analysis/tests
.\.venv\Scripts\python.exe -m pytest modules/dashboard/tests
```

`sanitizer_exception` is a valid test reason code but is produced by the ingest orchestrator's exception fallback, not by `sanitizer.py` itself. The root `unittest discover` command does not find the module-local tests in the current repository layout, so it is not a valid repository-wide validator. If the project later adds an explicit repository-wide test, lint, or type-check runner, run it before the database rebuild.

---

## 6. Disposable Development Database Rebuild

Perform this only after documentation changes, code changes, and all validators pass.

1. Confirm `data/canonical.db` is a development-only artifact and there is no data to retain.
2. Stop any process holding the database.
3. Delete `data/canonical.db` and any SQLite sidecars (`canonical.db-wal`, `canonical.db-shm`) as a deliberate development reset.
4. Create the fresh database in dependency order:

   ```text
   python -m modules.ingest.src.cli migrate --db-path data/canonical.db
   python -m modules.classify.src.cli migrate --db-path data/canonical.db
   python -m modules.curate.src.cli migrate --db-path data/canonical.db
   ```

   Run translate and publish migrations only if their smoke validation is included. Then ingest new records:

   ```text
   python -m modules.ingest.src.cli fetch --db-path data/canonical.db
   ```

   Never delete or alter `data/canonical_final.db`, which is a separate historical analysis database.
5. Verify the rebuilt database has the existing `source_item_text.text_processing_status` and `text_processing_reason` columns.
6. Ingest a controlled fixture or fresh feed items covering one `completed`, one allowed low-context, one `post_cleanup_empty`, and one `failed` item.
7. Verify classify selects the first two records only and writes a `classification_result` only after successful classification.

This reset is intentionally an exception to the normal durability guidance in `docs/DATA_LIFECYCLE.md`. It is valid because this environment is explicitly development-only and the database is being replaced, not migrated.

---

## 7. Post-Rebuild Observation

For the first one to two weeks after rebuild, use ad hoc SQL or the read-only `analysis` module to measure:

1. Classify distribution for selected low-context items: `core`, `adjacent`, `irrelevant`, and `unknown`.
2. Actual completed-classification volume. Token cost requires independent provider billing or runner telemetry because no current canonical table stores request-token usage.
3. Curate approval rate and `downstream_action` distribution for those items.
4. A manual sample of low-context items that reach `publish_summary`, checking that summary bullets are grounded in available text.
5. Per-source low-context reason distributions as source-health baselines.

Do not make a new pipeline rule from these observations alone. If summary fabrication is repeatedly observed, propose a separate, curate-owned policy and validation change. If classify unknown rates are unacceptably high, propose a separate, measured classify prompt experiment.

---

## 8. Investigation Status Update

After this plan is approved, update `known_issues/INGEST_LOW_CONTEXT_INVESTIGATION.md` without changing its evidence:

1. Change the document status to record option five as selected.
2. Mark §3 as not selected and §4 as the adopted direction.
3. Close §3.9 according to §4.8: low-context does not constrain curate actions or enter curate as routing metadata.
4. Replace the open historical-backlog choice with the approved disposable-development-database rebuild decision.

Historical working notes remain historical records and do not need retroactive edits.

---

## 9. Completion Criteria

The refactor is complete only when:

- all files in §3.1 and §3.2 state the same queue contract;
- no documentation claims that all low-context items bypass classify;
- `get_pending_items` uses the NULL-safe predicate in §1;
- tests prove allowed low-context reasons enter classify and the two exclusions do not;
- classify prompts still receive only title and sanitized text;
- curate source and documents are unchanged;
- the prior development database has been discarded and a fresh database passes the smoke verification;
- initial observation queries and manual sample criteria are recorded for the new data only.
