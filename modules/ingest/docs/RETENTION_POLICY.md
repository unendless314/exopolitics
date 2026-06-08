# Retention Policy

**Status:** Active rewrite draft  
**Updated:** 2026-06-08

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

The MVP policy shape should support at least:

- default raw retention

Default direction:

- most raw records use one bounded default class
- low-frequency special backup needs should be handled outside the main ingest retention flow when possible

External-ops direction:

- if selected sources or selected items require special backup beyond normal raw retention, prefer a separate cron job, export script, or manual archival step
- that external backup path does not need to be part of the canonical ingest schema in MVP

---

## 5. Cleanup Rules

Cleanup must support the following rules:

- raw retained records older than the configured retention window may be deleted
- cleanup must not delete the sanitized working text needed downstream
- cleanup actions should be auditable

---

## 6. Audit Expectations

Cleanup should preserve enough auditability to answer:

- which raw records were deleted
- when cleanup ran
- which retention class applied

This does not require permanent retention of the deleted payload itself.

---

## 7. Decisions Locked By This Rewrite

- raw retention is allowed but not indefinite by default
- sanitized working text remains durable even after raw cleanup
- cleanup is expected operation, not accidental data loss by itself
- low-frequency special backup needs may be handled outside the main ingest retention workflow
