# Refactoring Scope Analysis: Low-Context and Classification Boundary Cleanup (V2 Approved)

This document outlines the refactoring scope to pay down technical debt regarding **low-context item handling** and the **classification module boundary**.

---

## 1. Executive Summary

### 1.1 Core Problem
Under the current module design:
* `ingest` produces `source_item_text.is_low_context`.
* Low-context items still enter the `classify` queue.
* `classify` then performs a deterministic bypass and writes placeholder rows into `classification_result`.

This creates a dirty contract because `classification_result` is polluted with items that did not actually go through classify-stage processing, leaking into relevance-rate denominators, classify workload metrics, and latency analysis.

### 1.2 Approved V2 Solution
1. **Low-Context State Ownership**: Keep low-context as an ingest-owned sanitization outcome on `source_item_text` (`is_low_context = 1`, `low_context_reason = ...`).
2. **Immutability of Ingest Status**: Do not change `source_item.ingest_status` to `'low_context_bypassed'`. It remains `'ingested'`, keeping the status-based representation clean.
3. **Queue Selection Exclusion**: Update `classify` pending query to actively exclude low-context items (`is_low_context = 0`).
4. **Clean Classification Results**: Reserve `classification_result` strictly for items that actually completed classify-stage processing (primarily LLM classification, but allowing future rule-based non-LLM classify-stage classifiers).
5. **No Conflating Errors**: Sanitization failures remain engineering anomalies and are not re-labeled as low-context.

---

## 2. Refactoring Scope: Top-Level System Documentation (`docs/`)

### 2.1 [MODULE_BOUNDARIES.md](file:///C:/Users/user/Documents/exopolitics/docs/MODULE_BOUNDARIES.md)
* **Section 3.1 (`ingest`)**: Update to explicitly state that `ingest` owns low-context detection as part of sanitization output.
* **Section 3.2 (`classify`)**: Clarify that `classify` consumes sanitized working text only for items where `is_low_context = 0` and excludes low-context items at queue selection time. State that `classify` must not create placeholder classification rows.

### 2.2 [DATA_LIFECYCLE.md](file:///C:/Users/user/Documents/exopolitics/docs/DATA_LIFECYCLE.md)
* **Section 3 (Stage Flow)**: Revise the conceptual flow so low-context items branch out before `classification_result`:
  ```text
  raw feed item
    -> normalized ingest item
    -> sanitized working text
    -> [conditional boundary]
         |-- is_low_context = 1 -> stop before classify
         |-- is_low_context = 0 -> classification result
              -> curation decision
  ```
* **Section 6.3 (Failure Semantics)**: Clarify that low-context items do not generate canonical classification rows and stop before classify-stage processing in the current operational pipeline.
* **Section 12 (Lifecycle Questions)**: Add a lifecycle question stating that low-context items are excluded from classify-stage processing and do not generate downstream classification records.

### 2.3 [CANONICAL_ENTITY_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/docs/CANONICAL_ENTITY_CONTRACT.md)
* **Section 4.6 (Classification Result)**: Reserve it for items that actually enter and complete classify-stage processing (primarily LLM-based, but allowing future rule-based non-LLM classify-stage logic). Ingest-detected low-context bypasses are excluded.
* **Section 5 (Boundaries)**: Clarify that low-context items do not generate `Classification Result` entities and are bypassed before classify-stage processing.

---

## 3. Refactoring Scope: Ingest Module (`modules/ingest/`)

### 3.1 Documentation
* **[DATA_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/modules/ingest/docs/DATA_CONTRACT.md)** & **[SANITIZATION_STRATEGY.md](file:///C:/Users/user/Documents/exopolitics/modules/ingest/docs/SANITIZATION_STRATEGY.md)**:
  * Clarify that low-context items are excluded from the classify pending queue in the operational pipeline (stopping before classify), and that sanitization failures are engineering anomalies distinct from low-context content states.
* **[STORAGE_SCHEMA.md](file:///C:/Users/user/Documents/exopolitics/modules/ingest/docs/STORAGE_SCHEMA.md)**:
  * No status-based check changes; `source_item.ingest_status` remains strictly restricted to `'ingested'`.

### 3.2 Python Code & Tests
* No modifications to `source_item.ingest_status` are required.
* Ensure unit tests distinguish sanitization failures from low-context outcomes.

---

## 4. Refactoring Scope: Classify Module (`modules/classify/`)

### 4.1 Documentation
* **[CLASSIFICATION_POLICY.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/CLASSIFICATION_POLICY.md)**: Remove deterministic low-context bypass routing policy (Section 3).
* **[DATA_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/DATA_CONTRACT.md)**: Remove references to deterministic bypasses/nulls in Section 2, and update the pending queue query.
* **[IMPLEMENTATION_PLAN.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/IMPLEMENTATION_PLAN.md)** & **[README.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/README.md)**: Remove deterministic bypass tasks and references.

### 4.2 Python Code
* **[database.py](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/database.py)**: Update `get_pending_items` query:
  ```sql
  SELECT s.source_item_id, s.title, t.sanitized_text
  FROM source_item s
  JOIN source_item_text t ON s.source_item_id = t.source_item_id
  LEFT JOIN classification_result c ON s.source_item_id = c.source_item_id
  WHERE s.ingest_status = 'ingested'
    AND t.is_low_context = 0
    AND c.classification_result_id IS NULL
  LIMIT ?;
  ```
* **[orchestrator.py](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/orchestrator.py)**: Remove bypass routing conditional checks and arguments.

---

## 5. Downstream & Analysis Impact

### 5.1 Metrics Catalog (`modules/analysis/docs/METRICS_CATALOG.md`)
* **Relevance Rate**: Maintain denominator on `classification_result` since it now naturally excludes low-context bypasses.
* **SQLite timestamp subtraction**: Update latency formulas to use epoch-based subtraction `strftime('%s', A) - strftime('%s', B)`.
* **Zero-denominator protection**: Apply `NULLIF(denominator, 0)` in all ratio metrics.
* **Translation Completion SQL pattern**: Dynamically calculate target languages using a JSON array parameter `:target_languages_json` (e.g., `["en", "zh", "ja"]`) joined via `json_each` to count required target languages per article:
  ```sql
  (SELECT COUNT(*) FROM json_each(:target_languages_json) WHERE value != acr.content_language_code) AS required_translation_count
  ```
* **Run success rate**: Correct formulas to use actual schema columns `fetch_run.succeeded_source_count` and `fetch_run.attempted_source_count`.
