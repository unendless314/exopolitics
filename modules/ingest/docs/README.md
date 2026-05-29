# Ingest Module Docs

**Document version:** v0.2  
**Updated:** 2026-05-28  
**Status:** Active

---

## 1. Module Positioning

`ingest` is the first executable module in the pipeline:

`ingest -> classify -> review -> edit (when needed) -> publish -> site`

`ingest` owns feed fetching, normalization, dedup inputs, and persistence of raw intake metadata.  
It does not own topic judgment, review decisions, publishing output, or site rendering.

---

## 2. Documentation Map

Use the following docs as the source of truth for ingest scope and contracts:

1. `DATA_CONTRACT.md`
   Defines ingest input/output boundaries and record-level contracts.
2. `SOURCE_CONFIG_SCHEMA.md`
   Defines `modules/ingest/config/*.yaml` schema and validation rules.
3. `FETCH_EXECUTION.md`
   Defines fetch flow, shard model, schedule handling, retries, and cache headers.
4. `ERROR_POLICY.md`
   Defines failure taxonomy, source health behavior, quarantine/disable rules.
5. `OPERATIONS_RUNBOOK.md`
   Defines day-to-day operations, troubleshooting, and recovery procedures.

`INGEST_MVP_DRAFT.md` is retained as an early planning draft. New decisions should be written in the docs above.

---

## 3. Scope Guardrails

In scope:

- load and validate source config
- resolve due sources
- fetch feeds with bounded concurrency
- normalize entries and compute dedup keys
- write canonical ingest records and run metadata
- record source health and fetch outcomes

Out of scope:

- LLM calls
- publish-layer transformation
- editorial review logic
- site-facing rendering decisions

---

## 4. Read Order For New Contributors

1. Read `DATA_CONTRACT.md` first to understand ingest boundaries.
2. Read `SOURCE_CONFIG_SCHEMA.md` and inspect `modules/ingest/config/`.
3. Read `FETCH_EXECUTION.md` for runtime behavior.
4. Read `ERROR_POLICY.md` and `OPERATIONS_RUNBOOK.md` before changing production-facing behavior.
