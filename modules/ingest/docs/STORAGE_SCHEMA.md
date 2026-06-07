# Storage Schema Direction

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines the minimum ingest storage shape required by the rewrite.

It does not lock every final column name yet, but it does lock the minimum table set, logical relationships, and representation separation.

---

## 2. Minimum Table Set

The ingest schema must contain at least these logical tables:

1. `source_item`
2. `source_item_text`
3. `source_item_raw`
4. `source_state`
5. `fetch_run`
6. `fetch_attempt`
7. `ingest_dedup_marker`

Important rule:

- the rewritten schema must not overload a single `summary`-style column with both raw and sanitized meanings

---

## 3. Logical Roles

### 3.1 `source_item`

Durable canonical source identity and metadata.

Expected contents:

- source identity
- normalized title
- published timestamp
- fetched timestamp
- dedup key and rule
- ingest status

### 3.2 `source_item_text`

Durable sanitized downstream working text linked to `source_item`.

Expected contents:

- sanitized text body
- sanitization method or version
- HTML detected flag
- truncation flag
- low-context flag
- raw and sanitized length metrics

Default direction:

- one current text record per source item in MVP
- future multi-version re-sanitization is allowed only if later contracts introduce it explicitly

### 3.3 `source_item_raw`

Short-retention raw payload storage linked to `source_item`.

Expected contents:

- raw payload or fragment
- capture kind
- retained-at timestamp
- retention class
- exception retention marker when applicable

Default direction:

- one source item may have zero or more raw retained records

### 3.4 `source_state`

Current mutable source health and fetch state.

### 3.5 `fetch_run`

Run-level execution history.

### 3.6 `fetch_attempt`

Source-level execution outcome within a run.

Expected contents should include counts for:

- new items
- dedup matches
- item anomalies such as low-context or sanitization failures when tracked

### 3.7 `ingest_dedup_marker`

Explicit deduplication state for deterministic ingest identity control.

---

## 4. Required Relationships

- one `source_item` may have zero or one active `source_item_text` record in MVP
- one `source_item` may have zero or more `source_item_raw` records
- one `fetch_run` has many `fetch_attempt` records
- one `ingest_dedup_marker` must point to exactly one `source_item`

Important rules:

- canonical source identity must remain stable even after raw retained payloads expire
- sanitized text must be queryable independently from raw retained payloads
- raw cleanup must be possible without damaging downstream contracts
- mutable source state must remain operationally separate from immutable source item history

---

## 5. Schema Direction Rules

- normalized source identity and working text are separate records
- raw retained payloads are retention-governed and must not be the only copy of downstream text
- item-level sanitization outcomes should be representable without reinterpreting fetch-level status fields
- dedup state must stay explicit and auditable

---

## 6. Immediate Next Schema Task

The next schema task is to translate this direction into concrete DDL after implementation begins.

That DDL should preserve the separation defined here even if final column names differ from this document.
