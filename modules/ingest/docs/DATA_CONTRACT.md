# Ingest Data Contract

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines what `ingest` reads, what it writes, and which representations it is responsible for creating.

The core rule is:

- `ingest` does not stop at fetching feed metadata
- `ingest` must create the sanitized working representation used downstream
- downstream modules must not guess whether a field is raw or cleaned

---

## 2. Inputs

Required inputs:

- source configuration from `modules/ingest/config/`
- current execution time for due-source resolution
- prior source state
- prior dedup state

Optional inputs:

- operator-provided run scope such as source subsets or fetch groups
- manual recovery mode for selected sources
- retained raw records from recent runs when needed for sanitizer evaluation

`ingest` must not require `classify`, `curate`, `publish`, or `site` data as runtime inputs.

---

## 3. Outputs

`ingest` writes six logical output groups:

1. normalized source item records
2. sanitized working text records
3. sanitization metrics and flags
4. raw retained records when policy says to keep them
5. source state and fetch execution records
6. dedup state

`ingest` must not write:

- topic classification decisions
- curation decisions
- edit workflow records
- publish-layer output records

---

## 4. Representation Set

### 4.1 Normalized Source Item

This is the durable source-derived identity and metadata record.

Minimum logical fields:

- `source_id`
- `source_item_guid` nullable
- `canonical_url` nullable
- normalized `title`
- `published_at` nullable
- `fetched_at`
- `ingest_dedup_key`
- `dedup_rule`
- `ingest_status`

Important rule:

- this record must not overload a text field with both raw and sanitized meanings

### 4.2 Sanitized Working Text

This is the cleaned downstream working representation derived from raw feed input.

Minimum logical fields:

- stable link to the normalized source item
- sanitized text body
- sanitization method or version
- whether truncation occurred
- whether HTML was detected
- raw text length when measurable
- sanitized text length
- reduction ratio or equivalent metric when measurable
- low-context signal when cleaning leaves too little usable text
- created timestamp

Important rule:

- this is the default text representation for `classify` and curation support

### 4.3 Raw Retained Record

This is a retention-governed raw representation kept for debugging and validation.

Minimum logical fields when raw retention is enabled:

- stable link to the normalized source item
- raw payload or raw fragment
- retention class
- expiration timestamp when cleanup is scheduled by policy

Important rule:

- raw retained records are useful evidence, not the downstream working text contract

### 4.4 Source State

This is the mutable operational record for fetch health and source status.

Minimum logical fields:

- `source_id`
- `last_fetch_at`
- `last_success_at`
- `last_http_status`
- cache validators when available
- failure counters and current health state
- quarantine metadata when applicable

### 4.5 Fetch Run And Attempt

This is the execution history for ingest operations.

Minimum logical fields:

- run identifier
- run scope
- start and end timestamps
- source-level attempt outcomes
- source counts for success, failure, new items, dedup matches, and item anomalies
- error summary when present

### 4.6 Dedup State

This is the explicit record of ingest identity control.

Minimum logical fields:

- dedup key
- dedup rule
- stable link to the normalized source item
- created timestamp

**Implementation Note:** Deduplication key scope rules are defined as:
- `guid`, `tp`, and `fh` rules encode the `source_id` within the `dedup_key` (source-scoped) to prevent cross-source identity collisions from conflicting feed schemas.
- The `url` rule is cross-source (global) to avoid duplicate article ingestion when multiple feeds share identical canonical links.

---

## 5. Boundary Rules

`ingest` owns:

- source configuration interpretation
- feed fetching
- entry parsing
- source item normalization
- deduplication
- sanitization into working text
- raw retention handling under policy
- source health tracking

`ingest` must not own:

- classification prompts or judgments
- curation queue behavior
- publish formatting
- site rendering

Important boundary:

- `ingest` owns the transition from raw feed input to sanitized working text
- `classify` owns what to conclude from that text

---

## 6. Failure Semantics

- one source failure must not corrupt prior ingest records
- one item-level sanitization problem must not erase unrelated items from the same run
- a source item may still be stored even when raw retention is disabled
- later raw cleanup does not invalidate sanitized working records

If an item cannot produce useful sanitized working text, `ingest` should still preserve enough metadata and sanitization outcome data for downstream handling and investigation.

---

## 7. Decisions Locked By This Rewrite

- `ingest` writes both normalized source identity and sanitized working text
- raw retained payloads are optional evidence, not the default downstream text contract
- sanitized working text is created before classification
- raw retention is policy-driven and time-bounded by default
- downstream modules must not rely on ambiguous summary semantics
- text-processing outcome classification (`text_processing_status`: `completed`, `low_context`, `failed`) remains an ingest-owned sanitization outcome on `source_item_text` and downstream classification modules exclude non-completed items at queue selection time
