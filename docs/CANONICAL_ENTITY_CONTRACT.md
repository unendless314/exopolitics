# Canonical Entity Contract

**Status:** Active rewrite draft  
**Updated:** 2026-06-08

---

## 1. Purpose

This document defines the minimum canonical entity set shared across modules in the rewritten system.

It exists to lock:

- which long-lived entities exist in canonical storage
- which module owns each entity family
- which representations are safe for downstream modules to read

It does not lock:

- exact table names
- exact column names
- database-specific DDL
- module-internal execution details

---

## 2. Canonical Entity Principles

- canonical storage keeps the durable operational record
- raw input and sanitized working text are different entities with different retention expectations
- module ownership follows decision ownership, not convenient table placement
- downstream modules may read only explicitly defined canonical representations
- publish and site layers are downstream derivatives, not canonical writers

---

## 3. Minimum Canonical Entity Set

The rewritten system must recognize at least these canonical entity families:

1. source item identity and metadata
2. sanitized working text
3. raw retained evidence
4. source state and fetch execution history
5. ingest deduplication state
6. classification result
7. review decision
8. edit-owned draft or edited content when that workflow is active
9. publishable record or publish reference

The list above is a logical contract, not a requirement that every family live in a single table.

---

## 4. Entity Families

### 4.1 Source Item Identity And Metadata

This entity family represents the durable source-derived record created by `ingest`.

Minimum semantic contents:

- source identity
- feed item identity when available
- canonical source URL when available
- normalized title
- published timestamp when available
- ingest timestamps and dedup identity

Ownership:

- written by `ingest`
- readable by `classify`, `review`, and `publish`

Important rule:

- this entity family must not be the storage location for ambiguous mixed raw-versus-sanitized text semantics

### 4.2 Sanitized Working Text

This entity family represents the cleaned downstream working text derived during `ingest`.

Minimum semantic contents:

- stable link to the source item record
- sanitized text body
- sanitization method or version
- sanitization quality or low-context signals
- durable metrics needed after raw cleanup

Ownership:

- written by `ingest`
- readable by `classify` and `review`

Important rule:

- this is the default downstream text representation for operational workflow

### 4.3 Raw Retained Evidence

This entity family represents retention-governed raw payloads or fragments captured during `ingest`.

Minimum semantic contents:

- stable link to the source item record
- raw payload or fragment
- capture kind
- retained timestamp
- retention classification or exception marker

Ownership:

- written by `ingest`
- readable only when debugging, validation, or investigation requires it

Important rule:

- raw retained evidence is not the default downstream text contract

### 4.4 Source State And Fetch Execution History

This entity family represents mutable source health plus immutable run and attempt history.

Minimum semantic contents:

- current source fetch health
- cache validators when available
- run-level execution records
- source-attempt outcomes and counts

Ownership:

- written by `ingest`
- primarily operationally consumed by `ingest`

### 4.5 Ingest Deduplication State

This entity family represents explicit dedup identity control for source-derived items.

Minimum semantic contents:

- dedup key
- dedup rule
- stable link to the canonical source item

Ownership:

- written by `ingest`
- operationally consumed by `ingest`

### 4.6 Classification Result

This entity family represents the initial machine classification outcome.

Minimum semantic contents:

- stable link to the classified source item
- topic class
- confidence
- rationale or reason
- classifier metadata needed for auditability

Ownership:

- written by `classify`
- readable by `review` and `publish`

### 4.7 Review Decision

This entity family represents human approval, rejection, deletion, or edit-routing decisions.

Minimum semantic contents:

- stable link to the reviewed canonical record
- decision outcome
- responsible actor metadata
- decision timestamp
- optional notes or governance context

Ownership:

- written by `review`
- readable by `publish`

### 4.8 Edit-Owned Draft Or Edited Content

This entity family exists only when edited content becomes part of the active workflow.

Minimum semantic contents:

- edited or drafted text
- linkage to source-derived records when applicable
- edit responsibility and provenance metadata

Ownership:

- written by `edit`
- readable by `review` and `publish`

### 4.9 Publishable Record Or Publish Reference

This entity family represents the approved output selected for export.

Minimum semantic contents:

- approved canonical source reference
- export-ready provenance and disclosure fields
- publish-layer record identity or reference

Ownership:

- written by `publish`
- readable by `site`

---

## 5. Representation Boundaries

The top-level canonical model recognizes three non-interchangeable content representations:

1. raw retained evidence
2. sanitized working text
3. publish representation

Boundary rules:

- `classify` reads sanitized working text, not raw retained evidence by default
- `review` may inspect sanitized working text by default and raw retained evidence only when needed
- `site` reads publish-layer outputs only
- cleanup of raw retained evidence must not invalidate the source item record or sanitized working text record

---

## 6. Ownership Summary

- `ingest` owns source item identity, sanitized working text, raw retained evidence, source state, fetch history, and dedup state
- `classify` owns classification result
- `review` owns review decision
- `edit` owns edited content records when active in workflow
- `publish` owns publish-layer records or references
- `site` does not own canonical database writes

---

## 7. Decisions Locked By This Contract

- the canonical model separates source identity, sanitized working text, and raw retained evidence
- `ingest` is responsible for creating the sanitized working representation before classification
- downstream modules must not reinterpret ambiguous feed summary fields as canonical working text
- top-level docs lock entity families and ownership, while module docs lock implementation-facing schema details
