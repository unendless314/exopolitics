# Storage Schema Direction

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines the ingest storage direction after the reset.

It does not yet lock final column-by-column DDL, but it does lock the storage shape the implementation must follow.

---

## 2. Required Storage Separation

The ingest schema must separate at least these concerns:

1. normalized source item identity and metadata
2. sanitized working text and sanitization metrics
3. raw retained payloads under bounded retention
4. source state and source health
5. fetch run and fetch attempt history
6. dedup state

The rewritten schema must not overload a single `summary`-style column with both raw and sanitized meanings.

---

## 3. Logical Record Groups

### 3.1 Source Item

Durable canonical identity and source metadata.

### 3.2 Sanitized Text Record

Durable downstream working text linked to the source item.

### 3.3 Raw Retained Record

Short-retention raw payload storage linked to the source item.

### 3.4 Source State

Current mutable source health and fetch state.

### 3.5 Fetch Run And Attempt

Execution history for ingest operations.

### 3.6 Dedup Marker

Deterministic ingest identity control.

---

## 4. Schema Direction Rules

- canonical source identity must remain stable even if raw retained payloads expire
- sanitized text must be queryable independently from raw retained payloads
- raw retention cleanup must be possible without damaging downstream contracts
- source state should remain operationally separate from immutable source item history
- dedup state must stay explicit and auditable

---

## 5. Immediate Next Schema Task

The next active schema task is to translate this storage direction into a concrete table set and DDL after the ingest data contract and sanitization contract are accepted.
