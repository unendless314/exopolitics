# Ingest Data Contract

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines what `ingest` reads, what it writes, and which downstream representation it is responsible for creating.

The key contract change in this rewrite is:

- `ingest` does not stop at preserving raw feed metadata
- `ingest` must also create the sanitized working text used downstream
- downstream modules must not be forced to guess whether a field is raw or cleaned

This document complements the top-level contracts in `docs/` and is the active module-level source of truth for ingest output semantics.

---

## 2. Inputs

Required inputs:

- `modules/ingest/config/` source configuration
- current execution time for due-source resolution
- prior canonical source state such as fetch timestamps and health state
- prior dedup markers or equivalent ingest identity state

Optional inputs:

- operator-provided run filters such as source subsets or fetch groups
- manual recovery scope for selected sources

`ingest` may also temporarily observe retained raw input from recent prior runs for debugging or sanitizer evaluation, but that retained raw layer is not a required downstream input contract.

---

## 3. Outputs

`ingest` writes five logical output groups:

1. normalized source item records
2. sanitized working text records
3. raw retained records when policy says to keep them
4. source fetch metadata and source health state
5. fetch run, fetch attempt, and dedup state records

No output from `ingest` may directly encode:

- topic classification decisions
- review decisions
- publish-layer formatting

---

## 4. Representation Boundary

`ingest` is responsible for creating and separating at least these representations:

### 4.1 Normalized Source Item Representation

This is the canonical source-level identity and metadata record.

Examples:

- source identifier
- source item GUID when available
- canonical URL when available
- normalized title
- published timestamp when available
- fetched timestamp
- dedup key and dedup rule

This representation is durable and belongs in canonical long-term storage.

### 4.2 Sanitized Working Text Representation

This is the cleaned text derived from raw feed input for downstream use.

It exists so downstream modules can consume a predictable working representation without inheriting raw feed ambiguity.

This representation is durable and belongs in canonical long-term storage.

### 4.3 Raw Retained Representation

This is the raw feed payload or selected raw fragments preserved for bounded retention.

Examples:

- raw summary or description
- raw HTML fragments
- original feed text before sanitization

This representation is retention-governed and must not be treated as the default downstream working text contract.

---

## 5. Minimum Logical Output Contract

### 5.1 Normalized Source Item

Minimum logical fields:

- `source_id`
- `source_item_guid` nullable
- `canonical_url` nullable
- `title`
- `published_at` nullable
- `fetched_at`
- `ingest_dedup_key`
- `dedup_rule`
- `ingest_status`

Important rule:

- normalized source item records must not overload a single text field with both raw and cleaned meanings

### 5.2 Sanitized Working Text

Minimum logical fields:

- stable link to the normalized source item
- sanitized text body
- sanitization method or version
- whether truncation occurred
- whether HTML was detected
- raw text length when measurable
- sanitized text length
- reduction ratio or equivalent reduction metric when measurable
- low-context signal when cleaning leaves too little usable text
- created timestamp

Important rule:

- this is the default downstream text representation for `classify` and reviewer support

### 5.3 Raw Retained Record

Minimum logical fields when raw retention is enabled:

- stable link to the normalized source item
- raw text or raw fragment payload
- raw content type or capture kind
- retained-at timestamp
- retention class or exception marker when applicable

Important rule:

- raw retained records exist for debugging, validation, and exception handling
- they do not define downstream classification input semantics

### 5.4 Source State

Minimum logical fields:

- `source_id`
- `last_fetch_at`
- `last_success_at`
- `last_http_status`
- cache validators when available
- failure counters and current health state
- quarantine metadata when applicable

### 5.5 Fetch Run And Attempt Records

Minimum logical fields:

- run identifier
- run scope
- start and end timestamps
- source-level attempt outcomes
- source counts for success, failure, new items, and dedup matches
- error summary when present

---

## 6. Boundary Rules

`ingest` owns:

- feed fetching
- normalization of source item metadata
- deduplication
- creation of sanitized working text
- raw retention handling under policy
- source health tracking

`ingest` may update:

- ingest-layer records
- source health and fetch metadata
- dedup state
- retention-governed raw records

`ingest` must not update:

- topic classification fields
- review decision fields
- edit workflow fields
- publish-layer fields

Important boundary:

- `ingest` owns the transition from raw feed input to sanitized working text
- `classify` owns what to conclude from that text, not how to clean it

---

## 7. Failure Semantics

- fetch failure must not corrupt prior canonical ingest records
- parsing or sanitization failure for one source item must not erase source-level fetch history
- a source item may still be stored even if raw retention is disabled
- raw cleanup later does not invalidate the sanitized working representation already written

If a source item cannot produce usable sanitized working text, the ingest contract must still preserve enough metadata and sanitization outcome data for downstream handling and investigation.

---

## 8. Contract Direction Locked By This Rewrite

- `ingest` writes both normalized source identity and sanitized working text
- raw retained payloads are not the default downstream text contract
- sanitized working text is created before classification
- raw retention is policy-driven and time-bounded by default
- downstream modules must not rely on ambiguous summary semantics
