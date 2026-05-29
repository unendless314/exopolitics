# Ingest Data Contract

**Document version:** v0.1  
**Updated:** 2026-05-28  
**Status:** Draft

---

## 1. Purpose

Define what `ingest` reads, what it writes, and what it must not modify.  
This document is module-level and complements top-level docs in `docs/`.

---

## 2. Inputs

Required inputs:

- `modules/ingest/config/sources.yaml`
- `modules/ingest/config/categories.yaml`
- current execution time (for due-source resolution)
- prior canonical source state (health, last fetch timestamps, cache headers)

Optional inputs:

- operator-provided run filters (example: specific `fetch_group`)
- manual source override list for recovery runs

---

## 3. Outputs

`ingest` writes four logical output groups:

1. `source_item` records
2. source fetch metadata/state updates
3. fetch run / fetch attempt records
4. ingest dedup markers

No output in `ingest` may directly encode review or publish decisions.

---

## 4. Record Contract (Logical)

### `source_item`

Minimum logical fields:

- `source_id`
- `source_item_guid` (nullable if feed has none)
- `canonical_url`
- `title`
- `summary` (nullable)
- `published_at` (nullable)
- `fetched_at`
- `raw_payload_ref` or equivalent raw storage pointer
- `ingest_dedup_key`
- `ingest_status` (example: `ingested`)

### Source state

Minimum logical fields:

- `source_id`
- `last_fetch_at`
- `last_success_at`
- `last_http_status`
- `etag` (nullable)
- `last_modified` (nullable)
- health metrics (`consecutive_failures`, `last_error_code`, `quarantine_until` nullable)

### Fetch run / attempt

Minimum logical fields:

- run identifier
- start/end timestamps
- run scope (all / filtered shards)
- source-level outcome records
- error summary

---

## 5. Boundary Rules

`ingest` may update:

- `ingested`-level fields
- source health and fetch metadata
- ingest dedup metadata

`ingest` must not update:

- classification fields (`topic_class`, confidence, etc.)
- review states (`approved`, `rejected`, `deleted`)
- publish-layer fields

---

## 6. Dedup Contract (MVP)

MVP requires deterministic ingest-time dedup keys.  
Recommended precedence:

1. trusted feed GUID
2. normalized final URL
3. normalized title + published timestamp heuristic
4. source-scoped fallback hash

Conflict resolution policy is owned by ingest docs and tests, not by downstream modules.

---

## 7. Compatibility Rules

- Field additions should be backward compatible whenever possible.
- Existing semantic fields should not silently change meaning.
- Any contract-breaking change must include:
  - migration note
  - downstream impact note for `classify`
  - update to module tests and runbook
