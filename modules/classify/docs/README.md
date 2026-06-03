# Classify Module Documentation

**Document version:** v1.0  
**Updated:** 2026-06-03  
**Status:** Active  

---

## 1. Module Positioning

`classify` is the second executable module in the content aggregation pipeline:

`ingest -> classify -> review -> edit (when needed) -> publish -> site`

The module fetches pending `source_item` records (those with status `'ingested'` and no existing classification result), runs them through the LLM classifier, and writes the output classification parameters (such as `topic_class`, `classification_reason`, `classification_confidence`, and `edit_candidate` flags) to the canonical database.

It operates strictly on the boundary rules defined in `docs/MODULE_BOUNDARIES.md`. It does not handle RSS feeds, final publication decisions, or site generation.

---

## 2. Documentation Map

Use the following documents to understand classification scope, constraints, and DDL contracts:

1. **[DATA_CONTRACT.md](file:///C:/Users/user/documents/derived-work/modules/classify/docs/DATA_CONTRACT.md)**  
   Defines database contracts, DDL schema, status definitions, and constraint matrices for `classification_result`.
2. **[CLASSIFICATION_PROMPT.md](file:///C:/Users/user/documents/derived-work/modules/classify/docs/CLASSIFICATION_PROMPT.md)**  
   Defines model inputs, prompt structures, expected response formats, and classification criteria (`core`, `adjacent`, `irrelevant`).
3. **[BATCH_POLICY.md](file:///C:/Users/user/documents/derived-work/modules/classify/docs/BATCH_POLICY.md)**  
   Defines rate limits, batch size, recovery behavior, concurrency parameters, and SLA queue constraints.

---

## 3. Scope Guardrails

### In Scope:
* Load LLM configurations and prompt templates.
* Query the SQLite database for unclassified `source_item` records.
* Invoke the LLM with title/summary data to get classification judgments.
* Parse the LLM's structured JSON outputs.
* Save the results in `classification_result` tables transactionally.

### Out of Scope:
* Modifying original `source_item` data (strictly immutable).
* Initiating manual human review state transitions (owned by the `review` module).
* Direct HTML page construction or publishing files.
