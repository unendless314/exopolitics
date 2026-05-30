# Ingest Storage Schema Draft

**Document version:** v0.1  
**Updated:** 2026-05-30  
**Status:** Draft

---

## 1. Purpose

Translate the ingest logical contract into a storage-oriented draft that is concrete enough for implementation planning.

This document is still schema-direction only.  
It does not lock final SQLite DDL, migration filenames, or ORM structure.

---

## 2. Scope

MVP ingest storage should cover four main record groups:

1. source runtime state
2. run-level execution records
3. source-level attempt records
4. ingested source items and dedup support

Recommended logical records:

- `source_state`
- `fetch_run`
- `fetch_attempt`
- `source_item`
- `ingest_dedup_marker`

`source_definition` remains config-owned in MVP and does not need to be materialized as a canonical table on day one.

---

## 3. Design Direction

- prefer append-friendly run and attempt history
- keep source state mutable and current-value oriented
- keep source items immutable enough for auditability
- separate dedup support from run history so both concerns stay explainable
- avoid schema choices that make future Postgres migration harder

---

## 4. Logical Record Sketches

### 4.1 `source_state`

Purpose:

- store the latest operational state for each configured source

Recommended fields:

- `source_id`
- `last_fetch_at`
- `last_success_at`
- `last_http_status`
- `etag`
- `last_modified`
- `consecutive_failures`
- `last_error_code`
- `last_error_at`
- `health_status`
- `quarantine_until`
- `updated_at`

Key direction:

- primary key: `source_id`

Notes:

- exactly one current `source_state` record per source
- this record is updated by each completed source attempt

### 4.2 `fetch_run`

Purpose:

- record one ingest execution run

Recommended fields:

- `fetch_run_id`
- `started_at`
- `ended_at`
- `run_scope`
- `trigger_type`
- `run_status`
- `due_source_count`
- `attempted_source_count`
- `succeeded_source_count`
- `failed_source_count`
- `error_summary`
- `created_at`

Key direction:

- primary key: `fetch_run_id`

Notes:

- `run_status` should align with MVP values: `success`, `partial_failure`, `failed`
- `error_summary` may begin as structured JSON/text rather than a normalized child table

### 4.3 `fetch_attempt`

Purpose:

- record the outcome of one source within one fetch run

Recommended fields:

- `fetch_attempt_id`
- `fetch_run_id`
- `source_id`
- `attempt_started_at`
- `attempt_ended_at`
- `retry_count`
- `http_status`
- `error_class`
- `error_detail`
- `outcome`
- `new_item_count`
- `dedup_matched_count`
- `created_at`

Key direction:

- primary key: `fetch_attempt_id`
- foreign key direction: `fetch_run_id -> fetch_run.fetch_run_id`

Recommended uniqueness expectation:

- one final `fetch_attempt` record per source per run

Notes:

- internal retries do not need separate persisted rows in MVP
- `retry_count` is enough for early operational debugging
- `outcome` should align with MVP values: `success` or `failed`

### 4.4 `source_item`

Purpose:

- store normalized feed entries as canonical ingest records

Recommended fields:

- `source_item_id`
- `source_id`
- `source_item_guid`
- `canonical_url`
- `title`
- `summary`
- `published_at`
- `fetched_at`
- `raw_payload_ref`
- `ingest_dedup_key`
- `dedup_rule`
- `ingest_status`
- `created_at`

Key direction:

- primary key: `source_item_id`

Recommended index direction:

- index on `source_id`
- index on `published_at`
- index on `ingest_dedup_key`

Notes:

- `ingest_status` can start with `ingested`
- `dedup_rule` should indicate which rule produced the dedup key, such as `guid`, `canonical_url`, `title_published_at`, or `fallback_hash`

### 4.5 `ingest_dedup_marker`

Purpose:

- keep dedup identity explainable without overloading `source_item` lookup behavior

Recommended fields:

- `dedup_marker_id`
- `dedup_key`
- `dedup_rule`
- `source_item_id`
- `source_scope`
- `created_at`

Key direction:

- primary key: `dedup_marker_id`

Recommended uniqueness direction:

- unique on `dedup_key`

Notes:

- `source_scope` can remain nullable when the key is valid across sources
- if implementation later proves this table unnecessary, the same contract may be folded into a different structure, but dedup explainability should remain

---

## 5. Relationship Direction

Recommended relationships:

- one `fetch_run` to many `fetch_attempt`
- one `source_state` per `source_id`
- one `source_id` to many `source_item`
- one `source_item` to zero or more dedup-related matches over time
- one `ingest_dedup_marker` points to one canonical `source_item`

---

## 6. Mutability Direction

Recommended mutability model:

- `source_state`: mutable current state
- `fetch_run`: append-only after completion except for run finalization fields
- `fetch_attempt`: append-only after creation except for end-of-attempt finalization fields
- `source_item`: treat as effectively immutable after insert in MVP
- `ingest_dedup_marker`: append-only unless a repair procedure is explicitly run

This keeps debugging and audit trails simpler.

---

## 7. Minimal Integrity Rules

Suggested integrity rules for MVP:

- every `fetch_attempt` must belong to a `fetch_run`
- every `source_item` must have a non-null `source_id`
- every `source_item` must have a non-null `ingest_dedup_key`
- every `source_item` must have a non-null `fetched_at`
- every `ingest_dedup_marker` must point to an existing `source_item`
- every configured source should be able to have at most one current `source_state`

---

## 8. Deferred Decisions

The following should stay open until implementation pressure makes them necessary:

- whether `source_definition` should also be mirrored into the database
- whether retry events need their own table
- whether `error_summary` should become a normalized child structure
- whether `raw_payload_ref` points to file storage, blob storage, or in-db text
- whether cross-source dedup markers need separate uniqueness semantics
- final index set for SQLite performance

---

## 9. Recommended Next Step

Before writing persistence code, convert this draft into one concrete MVP schema decision set:

- exact field names
- nullable vs non-nullable rules
- unique constraints
- initial SQLite DDL
- migration strategy for future schema refinement
