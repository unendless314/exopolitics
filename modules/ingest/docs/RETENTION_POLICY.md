# Retention Policy

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines which ingest-layer records are long-term, which are short-retention, and what cleanup must preserve.

---

## 2. Long-Term Records

The following ingest-layer records belong in long-term canonical storage:

- normalized source item identity and metadata
- sanitized working text
- dedup state
- source state needed for ongoing operation
- fetch run and fetch attempt history
- sanitization metrics and flags needed for long-term observability

---

## 3. Short-Retention Records

The following records should be short-retention by default:

- raw summary or description payloads
- raw embedded HTML fragments
- oversized noisy raw text kept for sanitizer validation

These records are useful evidence, but they do not define the long-term downstream contract.

---

## 4. Retention Classes

The policy shape should support at least:

- default raw retention
- exception retention
- operator-frozen retention

Default direction:

- most raw records use the bounded default class
- special investigations may promote selected records into exception retention

---

## 5. Cleanup Rules

Cleanup must support the following rules:

- raw retained records older than the configured retention window may be deleted
- cleanup must not delete the sanitized working text needed downstream
- cleanup must respect exception-retained and operator-frozen records
- cleanup actions should be auditable

---

## 6. Audit Expectations

Cleanup should preserve enough auditability to answer:

- which raw records were deleted
- when cleanup ran
- which retention class applied
- whether any records were skipped due to exception retention

This does not require permanent retention of the deleted payload itself.

---

## 7. Decisions Locked By This Rewrite

- raw retention is allowed but not indefinite by default
- sanitized working text remains durable even after raw cleanup
- cleanup is expected operation, not accidental data loss by itself
- exception retention must exist without turning all raw data into permanent storage
