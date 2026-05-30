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

Logical records:

- `source_state`
- `fetch_run`
- `fetch_attempt`
- `source_item`
- `ingest_dedup_marker`

`source_definition` remains config-owned in MVP and does not need to be materialized as a canonical table on day one.
In MVP, `source_id` referential validity is guaranteed by ingest config validation (`modules/ingest/config/sources.yaml`) rather than a database `source_definition` table.

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

Fields:

- `source_id`
- `last_fetch_at`
- `last_success_at`
- `last_http_status`
- `etag`
- `last_modified`
- `consecutive_failures`
- `last_error_class`
- `last_error_at`
- `health_status`
- `quarantine_until`
- `updated_at`

Key direction:

- primary key: `source_id`

Notes:

- exactly one current `source_state` record per source
- this record is updated by each completed source attempt
- `last_error_class` uses the same taxonomy as `fetch_attempt.error_class`

### 4.2 `fetch_run`

Purpose:

- record one ingest execution run

Fields:

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
- `due_source_count` and `attempted_source_count` may differ when due sources are skipped before attempt start (for example disabled during run preparation, filtered by operator override, or excluded by quarantine policy)

### 4.3 `fetch_attempt`

Purpose:

- record the outcome of one source within one fetch run

Fields:

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

Uniqueness:

- enforce unique constraint on (`fetch_run_id`, `source_id`)
- this guarantees one final `fetch_attempt` record per source per run

Notes:

- internal retries do not need separate persisted rows in MVP
- `retry_count` is enough for early operational debugging
- `outcome` should align with MVP values: `success` or `failed`

### 4.4 `source_item`

Purpose:

- store normalized feed entries as canonical ingest records

Fields:

- `source_item_id`
- `source_id`
- `source_item_guid`
- `canonical_url`
- `title`
- `summary`
- `published_at`
- `fetched_at`
- `ingest_dedup_key`
- `dedup_rule`
- `ingest_status`
- `created_at`

Key direction:

- primary key: `source_item_id`

Index direction:

- index on `source_id`
- index on `published_at`
- index on `ingest_dedup_key`

Notes:

- `ingest_status` can start with `ingested`
- `dedup_rule` should indicate which rule produced the dedup key, such as `guid`, `url`, `tp`, or `fh`
- MVP does not persist raw payload snapshots in canonical ingest storage; debugging relies on normalized fields and fetch/run error records

### 4.5 `ingest_dedup_marker`

Purpose:

- keep dedup identity explainable without overloading `source_item` lookup behavior

Fields:

- `dedup_marker_id`
- `dedup_key`
- `dedup_rule`
- `source_item_id`
- `created_at`

Key direction:

- primary key: `dedup_marker_id`

Uniqueness:

- unique on `dedup_key`

Notes:

- MVP dedup markers use a single global `dedup_key` namespace
- `dedup_key` must be rule-prefixed to prevent cross-rule key collisions (for example `guid:<value>`, `url:<value>`, `tp:<value>`, `fh:<value>`)
- if implementation later proves this table unnecessary, the same contract may be folded into a different structure, but dedup explainability should remain

---

## 5. Relationship Direction

Relationships:

- one `fetch_run` to many `fetch_attempt`
- one `source_state` per `source_id`
- one `source_id` to many `source_item`
- one `source_item` to zero or more dedup-related matches over time
- one `ingest_dedup_marker` points to one canonical `source_item`

---

## 6. Mutability Direction

Mutability model:

- `source_state`: mutable current state
- `fetch_run`: append-only after completion except for run finalization fields
- `fetch_attempt`: append-only after creation except for end-of-attempt finalization fields
- `source_item`: treat as effectively immutable after insert in MVP
- `ingest_dedup_marker`: append-only unless a repair procedure is explicitly run

This keeps debugging and audit trails simpler.

---

## 7. Minimal Integrity Rules

Integrity rules for MVP:

- every `fetch_attempt` must belong to a `fetch_run`
- every (`fetch_run_id`, `source_id`) pair in `fetch_attempt` must be unique
- every `source_id` used by `source_state`, `fetch_attempt`, and `source_item` must exist in validated ingest source config at run start
- every `source_item` must have a non-null `source_id`
- every `source_item` must have a non-null `ingest_dedup_key`
- every `source_item` must have a non-null `fetched_at`
- every `ingest_dedup_marker` must point to an existing `source_item`
- every configured source should be able to have at most one current `source_state`

---

## 8. MVP Decisions (Locked)

The following decisions are finalized for MVP implementation:

- `source_definition` remains config-owned and is not mirrored into the database in MVP
- retry events do not get a separate table in MVP; `fetch_attempt.retry_count` is the MVP contract
- `error_summary` remains a single structured JSON/text field in `fetch_run` for MVP
- SQLite index strategy in MVP uses the current baseline indexes in this document; index expansion is deferred until measured performance pressure appears

---

## 9. Next Step

Before writing persistence code, convert this draft into one concrete MVP schema decision set:

- implementation checklist: `modules/ingest/docs/STORAGE_SCHEMA_LOCK_CHECKLIST.md`
- exact field names
- nullable vs non-nullable rules
- unique constraints
- initial SQLite DDL
- migration strategy for future schema refinement
