# Classify Module Documentation

**Document version:** v2.0  
**Updated:** 2026-06-03  
**Status:** Active

---

## 1. Module Positioning

`classify` is the second executable module in the pipeline:

`ingest -> classify -> review -> edit (when needed) -> publish -> site`

The module reads `source_item` rows that have been ingested but do not yet have a `classification_result`, evaluates them using feed metadata, and writes the initial classification result into the canonical database.

Its responsibilities are limited to:

* topic classification
* low-context detection
* `edit_candidate` tagging
* prompt/model selection
* execution controls such as batching, retries, and timeouts

It does not own manual review decisions, publication decisions, or site output.

---

## 2. MVP Summary

The current MVP rules are:

* `topic_class` supports `core`, `adjacent`, `irrelevant`, and `unknown`.
* `unknown` is a formal classification result, not an error state.
* `unknown` may be produced in two ways:
  * deterministic pre-checks when feed metadata is too short
  * LLM judgment when the text is long enough but still too ambiguous to classify reliably
* The schema does not include `classification_status`.
* The presence of a `classification_result` row is sufficient to indicate that an item has completed an initial classification pass.
* If an LLM call fails after retries, no `classification_result` row is written for that item.

---

## 3. Document Map

1. **`DATA_CONTRACT.md`**  
   Database schema, DDL, pending-item query rules, and persistence semantics.

2. **`CLASSIFICATION_POLICY.md`**  
   The meaning of `core`, `adjacent`, `irrelevant`, and `unknown`, plus low-context and `edit_candidate` rules.

3. **`PROMPT_CONTRACT.md`**  
   LLM input fields, prompt boundaries, and output schema.

4. **`EXECUTION_POLICY.md`**  
   Batch size, concurrency, timeout, retry, and run behavior.

---

## 4. Archive Note

The earlier planning files were preserved under `docs/archive/` as discussion history. They are not the active implementation contract.
