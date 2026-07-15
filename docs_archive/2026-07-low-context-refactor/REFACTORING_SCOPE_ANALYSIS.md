# Refactoring Scope Analysis: Text-Processing Status Contract and Classification Boundary Cleanup (V2 Approved)

This document outlines the refactoring scope to pay down technical debt regarding **low-context item handling** and the **classification module boundary**.

---

## 1. Executive Summary

### 1.1 Core Problem
Under the current module design:
* `ingest` produces `source_item_text.is_low_context`, which is a boolean that cannot distinguish content sparsity from text-processing failures.
* Low-context items still enter the `classify` queue.
* `classify` then performs a deterministic bypass and writes placeholder rows into `classification_result`.

This creates a dirty contract because `classification_result` is polluted with items that did not actually go through classify-stage processing, leaking into relevance-rate denominators, classify workload metrics, and latency analysis.

### 1.2 Approved V2 Solution
1. **Text-Processing Outcome Ownership**: Replace `is_low_context` / `low_context_reason` with `text_processing_status` / `text_processing_reason` on `source_item_text`, providing a three-state model (`completed`, `low_context`, `failed`).
2. **Immutability of Ingest Status**: Do not change `source_item.ingest_status` to `'low_context_bypassed'`. It remains `'ingested'`, keeping the status-based representation clean.
3. **Queue Selection Exclusion**: Update `classify` pending query to select only `text_processing_status = 'completed'` items.
4. **Clean Classification Results**: Reserve `classification_result` strictly for items that actually completed classify-stage processing (primarily LLM classification, but allowing future rule-based non-LLM classify-stage classifiers).
5. **No Conflating Errors**: Sanitization failures are recorded as `text_processing_status = 'failed'` with distinct reason codes, never as `low_context`.

---

## 2. Refactoring Scope: Top-Level System Documentation (`docs/`)

### 2.1 [MODULE_BOUNDARIES.md](file:///C:/Users/user/Documents/exopolitics/docs/MODULE_BOUNDARIES.md)
* **Section 3.1 (`ingest`)**: Update to explicitly state that `ingest` owns text-processing outcome classification (`completed`, `low_context`, `failed`) as part of sanitization output.
* **Section 3.2 (`classify`)**: Clarify that `classify` consumes sanitized working text only for items where `text_processing_status = 'completed'` and excludes non-completed items at queue selection time. State that `classify` must not create placeholder classification rows.

### 2.2 [DATA_LIFECYCLE.md](file:///C:/Users/user/Documents/exopolitics/docs/DATA_LIFECYCLE.md)
* **Section 3 (Stage Flow)**: Revise the conceptual flow so non-completed items branch out before `classification_result`:
  ```text
  raw feed item
    -> normalized ingest item
    -> sanitized working text
    -> [text_processing_status boundary]
         |-- low_context -> stop before classify
         |-- failed      -> stop before classify
         |-- completed   -> classification result
              -> curation decision
  ```
* **Section 6.3 (Failure Semantics)**: Clarify that items with `text_processing_status` of `low_context` or `failed` do not generate canonical classification rows and stop before classify-stage processing.
* **Section 12 (Lifecycle Questions)**: Add a lifecycle question stating that non-completed items are excluded from classify-stage processing and do not generate downstream classification records.

### 2.3 [CANONICAL_ENTITY_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/docs/CANONICAL_ENTITY_CONTRACT.md)
* **Section 4.6 (Classification Result)**: Reserve it for items where `text_processing_status = 'completed'` that actually enter and complete classify-stage processing. Items with status `low_context` or `failed` are excluded.
* **Section 5 (Boundaries)**: Clarify that non-completed items do not generate `Classification Result` entities and are bypassed before classify-stage processing.

---

## 3. Refactoring Scope: Ingest Module (`modules/ingest/`)

### 3.1 Documentation
* **[DATA_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/modules/ingest/docs/DATA_CONTRACT.md)** & **[SANITIZATION_STRATEGY.md](file:///C:/Users/user/Documents/exopolitics/modules/ingest/docs/SANITIZATION_STRATEGY.md)**:
  * Replace `is_low_context` / `low_context_reason` semantics with `text_processing_status` / `text_processing_reason`. Document the three-state model. Clarify that sanitization failures use `text_processing_status = 'failed'` and are distinct from `low_context` content states.
* **[STORAGE_SCHEMA.md](file:///C:/Users/user/Documents/exopolitics/modules/ingest/docs/STORAGE_SCHEMA.md)**:
  * Replace `is_low_context` / `low_context_reason` column definitions with `text_processing_status` / `text_processing_reason`. `source_item.ingest_status` remains strictly restricted to `'ingested'`.

### 3.2 Python Code & Tests
* No modifications to `source_item.ingest_status` are required.
* Update `sanitizer.py` return keys from `is_low_context` / `low_context_reason` to `text_processing_status` / `text_processing_reason`.
* Update `orchestrator.py` san_err handler to write `text_processing_status = 'failed'` instead of faking low-context.
* Update `database.py` INSERT SQL to use new column names.
* Ensure unit tests cover all three states and distinguish sanitization failures from low-context outcomes.

---

## 4. Refactoring Scope: Classify Module (`modules/classify/`)

### 4.1 Documentation
* **[CLASSIFICATION_POLICY.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/CLASSIFICATION_POLICY.md)**: Replace exclusion policy to reference `text_processing_status != 'completed'`.
* **[DATA_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/DATA_CONTRACT.md)**: Update pending queue query to filter on `text_processing_status = 'completed'`.
* **[IMPLEMENTATION_PLAN.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/IMPLEMENTATION_PLAN.md)** & **[README.md](file:///C:/Users/user/Documents/exopolitics/modules/classify/docs/README.md)**: Replace all `is_low_context` references with `text_processing_status` equivalents.

### 4.2 Python Code
* **[database.py](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/database.py)**: Update `get_pending_items` query:
  ```sql
  SELECT s.source_item_id, s.title, t.sanitized_text
  FROM source_item s
  JOIN source_item_text t ON s.source_item_id = t.source_item_id
  LEFT JOIN classification_result c ON s.source_item_id = c.source_item_id
  WHERE s.ingest_status = 'ingested'
    AND t.text_processing_status = 'completed'
    AND c.classification_result_id IS NULL
  LIMIT ?;
  ```
* **[orchestrator.py](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/orchestrator.py)**: Remove bypass routing conditional checks and arguments. Preserve dry-run transaction semantics.
* **[config.py](file:///C:/Users/user/Documents/exopolitics/modules/classify/src/config.py)** and **[model_settings.yaml](file:///C:/Users/user/Documents/exopolitics/modules/classify/config/model_settings.yaml)**: Remove `DeterministicClassification` model, `deterministic_classification` schema entry, and `self.deterministic` assignment.

---

## 5. Downstream & Analysis Impact

### 5.1 Metrics Catalog (`modules/analysis/docs/METRICS_CATALOG.md`)
* **Relevance Rate**: Maintain denominator on `classification_result` since it now naturally excludes non-completed items.
* **Low-Context and Failure Metrics**: Update references from `is_low_context` to `text_processing_status = 'low_context'` and add `text_processing_status = 'failed'` as a distinct analysis population.
* **SQLite timestamp subtraction**: Update latency formulas to use epoch-based subtraction `strftime('%s', A) - strftime('%s', B)`.
* **Zero-denominator protection**: Apply `NULLIF(denominator, 0)` in all ratio metrics.
* **Translation Completion SQL pattern**: Dynamically calculate target languages using a JSON array parameter `:target_languages_json` (e.g., `["en", "zh", "ja"]`) joined via `json_each` to count required target languages per article:
  ```sql
  (SELECT COUNT(*) FROM json_each(:target_languages_json) WHERE value != acr.content_language_code) AS required_translation_count
  ```
* **Run success rate**: Correct formulas to use actual schema columns `fetch_run.succeeded_source_count` and `fetch_run.attempted_source_count`.
* **Data Dependencies**: Replace `is_low_context` / `low_context_reason` with `text_processing_status` / `text_processing_reason` in required columns.
