# Retention Policy

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines which ingest-layer records are long-term, which are short-retention, and how cleanup must behave.

---

## 2. Long-Term Records

The following ingest-layer records belong in long-term canonical storage:

- normalized source item identity and metadata
- sanitized working text
- dedup state
- source state and source health history needed for operation
- fetch run and fetch attempt records
- sanitization metrics and flags needed for long-term observability

---

## 3. Short-Retention Records

The following records should be short-retention by default:

- raw summary or description payloads
- raw embedded HTML fragments
- oversized noisy raw text kept for sanitizer validation

These records are useful, but they do not define the long-term downstream contract.

---

## 4. Cleanup Rules

Cleanup must support the following rules:

- raw retained records older than the configured retention window may be deleted
- cleanup must not delete the sanitized working text needed downstream
- cleanup must respect exception-retained records
- cleanup actions should be auditable

---

## 5. Exception Retention

Some raw records may need longer retention.

Examples:

- sanitizer investigation samples
- parsing anomaly investigations
- classification dispute investigations
- operator-frozen records

The policy shape must support these exceptions without turning all raw data into permanent storage.

---

## 6. Direction Locked By This Rewrite

- raw retention is allowed but not indefinite by default
- sanitized working text remains durable even after raw cleanup
- cleanup is an expected operation, not accidental data loss by itself
