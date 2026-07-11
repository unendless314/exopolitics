# Known Issue: Metrics Catalog Contract Gaps and Low-Context Classification Boundary

## 1. Issue Summary

Final review of `modules/analysis/docs/METRICS_CATALOG.md` found several metric-contract issues where the documented formulas either do not match SQLite behavior, do not fully match the current schema, or leave important metric semantics ambiguous.

This review also confirmed a broader architectural mismatch around low-context items:

* Current production-like data in `data/canonical.db` shows that low-context items can still have rows in `classification_result`.
* The preferred future direction is to tighten the module boundary so that `classification_result` contains only items that were actually processed by the `classify` module and actually incurred LLM classification work.
* Under that future model, low-context items should be handled and terminated inside `ingest`, without creating canonical `classification_result` rows.

---

## 2. Confirmed Metric Catalog Problems

### 2.1 Timestamp subtraction is incorrect in SQLite

Affected metrics:

* `4.1 Pipeline Lead Time (E2E Latency)`
* `4.2.1 Feed Freshness Delay`
* `4.2.2 Fetch Execution Latency`
* `4.2.3 Classification Delay`
* `4.2.4 Curation Delay`
* `4.2.5 Translation Delay`
* `4.2.6 Publish Delay`

Problem:

* The catalog currently documents latency formulas in the form `A - B` where both fields are SQLite `TEXT` timestamps.
* Direct subtraction on these ISO8601 text fields does not produce valid elapsed seconds.

Database verification:

* Example direct subtraction output from `source_item.fetched_at - source_item.published_at`: `0`, `1`, `2`
* Example correct epoch-based subtraction using `strftime('%s', fetched_at) - strftime('%s', published_at)`: `4943826`, `10290426`, `47083866`

Required fix:

* All latency formulas and implementation guidance must use `strftime('%s', A) - strftime('%s', B)` or an equivalent epoch conversion before subtraction.

Severity:

* High

### 2.2 Translation completion SQL pattern contradicts its own notes

Affected metric:

* `4.3.2 Translation Completion Rate`

Problem:

* The metric notes correctly state that implementations must not assume a fixed target-language count.
* The included SQL pattern still hardcodes `:target_language_count - 1` as the required translation count.

Current database observation:

* `approved_content_record.content_language_code` is currently only `en`
* `translation_output` currently contains `en` bypass rows and translated `zh` and `ja` rows

Implication:

* The hardcoded subtraction happens to fit the current data shape, but it is still the wrong long-term contract because it cannot safely handle per-article variation in required target languages.

Required fix:

* Replace the SQL pattern with logic that computes required non-bypass target languages per article instead of assuming a fixed count.

Severity:

* Medium

### 2.3 Relevance rate denominator needs explicit semantic definition

Affected metric:

* `2.3 Relevance Rate`

Problem:

* The documented denominator is `Total Classified (items with a row in classification_result)`.
* This becomes ambiguous if low-context items can also receive fallback classification rows.

Current database observation:

* `source_item` count: `8072`
* `source_item_text.is_low_context = 1` count: `2008`
* `classification_result` count by `source_item_id`: `8072`

Implication:

* In the current database, low-context items are not excluded from the classification-result population.
* This is queryable, but the metric contract must explicitly state whether relevance is measured over:
  * all canonical classification rows, including rule/fallback rows, or
  * only items that actually entered LLM-backed classification.

Required fix:

* Clarify denominator semantics in the metric contract.
* Align the definition with the long-term architecture decision described in Section 4 below.

Severity:

* Medium

### 2.4 Zero-denominator protection is missing from ratio metrics

Affected metrics:

* `3.2 Classification Filtering Overhead`
* `3.4 Approval Rate by Content Density`

Problem:

* The catalog formulas do not explicitly require `NULLIF(...)` or equivalent zero-denominator protection.
* Some source slices or time windows can legitimately have zero approved items or zero items in a density bucket.

Required fix:

* Document defensive ratio handling using `NULLIF(denominator, 0)` or equivalent application-side protection.

Severity:

* Medium

### 2.5 Run success rate formula should use actual schema field names

Affected metric:

* `1.2 Run Success Rate`

Problem:

* The formula currently describes `Successful Source Attempts in fetch_run`.
* The actual schema field is `fetch_run.succeeded_source_count`.

Current schema verification:

* `fetch_run.attempted_source_count`
* `fetch_run.succeeded_source_count`
* `fetch_run.failed_source_count`

Required fix:

* Update the documented formula to use the real schema field names.

Severity:

* Low

---

## 3. Supporting Contract Mismatch in Data Dependencies

`modules/analysis/docs/DATA_DEPENDENCIES.md` also under-specifies several fields that the current schema already exposes and that downstream implementers are likely to need.

Examples:

* `fetch_run` currently includes `attempted_source_count`, `succeeded_source_count`, and related status fields.
* `curation_decision` currently includes `curate_status`, which is required for approval-vs-rejection metrics.
* `translation_output` also includes `source_item_id` in the live schema.

This is not the main defect, but it increases the risk of implementers writing metric SQL against imprecise or outdated field assumptions.

---

## 4. Long-Term Architecture Direction for Low-Context Items

Recommended future contract:

* `classification_result` should contain only items that were actually processed by the `classify` module.
* In practice, this means rows should represent real classification work, including actual LLM invocation or whatever canonical classify-stage processing is formally defined.
* Low-context items should be blocked and concluded inside `ingest`.
* Low-context items should not create canonical `classification_result` rows.

Rationale:

* Preserves a clean module boundary between `ingest` and `classify`
* Prevents rule-based fallback rows from being confused with real classify outputs
* Makes metrics such as relevance, topic breakdown, classify latency, and classify workload semantically cleaner
* Avoids mixing `insufficient context` with `classified as unknown` or other actual classify outcomes

Operational implication:

* UI and analysis layers must treat low-context as an explicit pre-classify terminal state instead of inferring it from fallback `classification_result` rows.

Suggested observable state model:

* `source_item_text.is_low_context = 1`
* `source_item_text.low_context_reason`
* optional higher-level reporting state such as `low_context_bypassed` or equivalent runtime-derived presentation state

---

## 5. Follow-Up Actions

1. Update `modules/analysis/docs/METRICS_CATALOG.md` to correct the five confirmed metric issues listed above.
2. Update `modules/analysis/docs/DATA_DEPENDENCIES.md` so required columns match the actual schema more precisely.
3. If and when the pipeline is refactored, revise the canonical contracts so `classification_result` is reserved for true classify-stage outputs only.
4. After that refactor, re-baseline `Relevance Rate`, topic breakdown, and any classify-stage funnel metrics so their denominators no longer include low-context bypass items.

---

## 6. Recorded Decision: Fix Upstream Before Completing Analysis

Decision recorded on review:

* Do not force completion of the `analysis` module against the current low-context/classification contract.
* First pay down the upstream technical debt around low-context handling and classification boundaries.
* Only continue full `analysis` module implementation after the upstream data model and pipeline semantics have been cleaned up.

Reasoning:

* Completing `analysis` now would harden the current ambiguous contract into metric SQL, report semantics, and CLI behavior.
* Once `classification_result` is later restricted to true classify-stage outputs only, major parts of the analysis layer would need to be rewritten anyway.
* The system has only been live for about two weeks, and the current database is considered disposable enough to permit a clean rebuild.

Planned execution order:

1. Refactor low-context handling so low-context items terminate inside `ingest` and do not create canonical `classification_result` rows.
2. Rebuild or replace `data/canonical.db`.
3. Re-run the full upstream pipeline on the new contract.
4. Update affected docs and metric definitions.
5. Resume `analysis` module implementation on top of the cleaned contract.

Temporary planning implication:

* Any analysis work performed before the refactor should be treated as exploratory only and should avoid becoming the long-term canonical implementation.
