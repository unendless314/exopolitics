# Fetch Execution

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines the runtime responsibilities of ingest fetch execution at a high level.

It focuses on execution shape, not final CLI syntax.

---

## 2. Core Responsibilities

Fetch execution owns:

- selecting due sources
- applying operator-provided run scope when present
- fetching feed payloads with bounded concurrency
- parsing feed entries
- creating normalized source item records
- creating sanitized working text
- recording source-level outcomes and source health updates

---

## 3. Expected Flow

```text
select due sources
  -> fetch source payload
  -> parse entries
  -> normalize source items
  -> sanitize working text
  -> persist ingest-layer records
  -> update source state and run records
```

---

## 4. Failure Expectations

- one source failure must not abort the entire run by default
- one item-level sanitization problem must be captured without corrupting unrelated items
- source health state must reflect repeated failures
- fetch execution must preserve enough evidence for later debugging

---

## 5. Out Of Scope

Fetch execution does not own:

- classification retries or batch policy
- review queue behavior
- edit workflow behavior
- publish formatting
