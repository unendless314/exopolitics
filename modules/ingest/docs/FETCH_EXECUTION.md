# Fetch Execution

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines the runtime behavior of ingest fetch execution.

It focuses on execution semantics and failure handling, not final CLI syntax.

---

## 2. Core Responsibilities

Fetch execution owns:

- selecting due sources
- applying operator-provided run scope when present
- fetching feed payloads with bounded concurrency
- parsing feed entries
- normalizing source items
- creating sanitized working text
- recording item-level anomalies when they occur
- recording source-level outcomes and source health updates

---

## 3. Expected Flow

```text
load config
  -> validate config
  -> resolve due sources
  -> fetch source payload
  -> parse entries
  -> normalize source items
  -> sanitize working text
  -> persist source items, sanitized text, and raw retained records when policy allows
  -> update source state and run records
```

---

## 4. Concurrency And Isolation

- source fetching must use bounded concurrency
- failure in one source must not cancel unrelated sources by default
- one source attempt should have a clear transaction boundary for persistence
- run-level failures should be reserved for configuration, storage, or other systemic failures

---

## 5. Item-Level Outcome Semantics

For each parsed entry, `ingest` should distinguish at least these outcomes:

1. new item with usable sanitized text
2. dedup match
3. new item with low-context sanitized text
4. item-level sanitization failure
5. item-level parse or normalization failure

Default direction:

- a dedup match should not create a new source item
- a low-context result may still create a source item and sanitized text record
- an item-level sanitization failure should be captured without hiding the source-level fetch outcome

Important rule:

- source-level success does not require every item to be equally useful downstream

---

## 6. Source-Level Outcome Semantics

Each source attempt should capture:

- HTTP result when available
- retry count
- final source-level outcome
- new item count
- dedup matched count
- item anomaly counts when tracked
- error class and detail when failed

Allowed non-null error classes are `network_error`, `timeout_error`,
`http_error_4xx`, `http_error_5xx`, `parse_error`, and `unexpected_error`.
`error_class` records a coarse failure category; `http_status` records the
precise HTTP-layer cause when available.

Default direction:

- successful fetch plus some low-context items is still a source-level success
- successful fetch plus some item-level sanitization failures may still be source-level success if the source payload was processed and anomalies were recorded
- transport, parse, or persistence failures that prevent meaningful processing should be source-level failures

Observability note:

- item-level sanitization and normalization failure counts are engineering signals for rule coverage and source structure quality
- those counts should be read as trend indicators, not as rigid business-state judgments

---

## 7. Failure Expectations

- one source failure must not abort the whole run by default
- one item-level failure must not corrupt unrelated items from the same source
- source health must reflect repeated source-level failures, not merely low-context content
- execution must preserve enough evidence for later debugging

An invalid error-class value at either persistence boundary is a programming
error. It must escape source-level failure isolation, fail the run, and leave
other sources able to complete. The violating source must not gain a new
failure attempt or increment its committed failure count. Non-contract
persistence failures may be recorded as `unexpected_error`; when a failed
fetch result exists, their detail should retain only its original error class
and HTTP status, never the remote response excerpt.

---

## 8. Out Of Scope

Fetch execution does not own:

- classification retry policy
- curation queue behavior
- edit workflow behavior
- publish formatting

---

## 9. Decisions Locked By This Rewrite

- fetch execution includes sanitization as part of ingest completion
- item-level anomalies must be representable without forcing a second module
- source-level success and item usefulness are related but not identical states
- a feed payload that feedparser reports as ill-formed (`bozo`) with a genuine
  syntax failure (a SAX parse error) is a source-level `parse_error`, even when
  partial entries were extractable; recoverable `bozo` conditions (for example
  feedparser's character-encoding override recovery) are not parse failures and
  their entries must be processed normally; a structurally valid payload with
  zero entries is not a parse failure
