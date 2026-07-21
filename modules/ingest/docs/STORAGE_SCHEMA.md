# Storage Schema

**Status:** Active rewrite draft  
**Updated:** 2026-06-08

---

## 1. Purpose

This document defines the implementation-facing logical table design for the rewritten `ingest` module.

It locks:

- the minimum table set
- logical columns and nullability direction
- primary keys and foreign key direction
- uniqueness rules
- index direction
- cleanup-safe retention boundaries

It does not yet lock:

- final migration filenames
- exact SQL dialect syntax
- every low-level database optimization choice

---

## 2. Storage Design Principles

- `source_item` stores durable source-derived identity and metadata
- `source_item_text` stores the durable sanitized working representation
- `source_item_raw` stores retention-governed raw evidence only
- mutable source health is separate from immutable item and run history
- item-level anomalies must be representable without overloading source-level status fields
- raw cleanup must be possible without damaging downstream reads

Important rule:

- the rewritten schema must not overload a single `summary`-style column with both raw and sanitized meanings

---

## 3. Shared Type And Key Direction

Recommended baseline direction:

- surrogate primary keys use integer identifiers
- source-config-owned identifiers such as `source_id` remain config-owned values, not database-generated values
- timestamps use one normalized UTC representation consistently across the module
- bounded enums may be stored as text with application validation and database checks where practical

MVP physical-direction rules for the first implementation:

- surrogate keys should use one consistent integer primary-key type across all ingest tables
- `source_id` should use the same integer type used by config validation and runtime models
- enum-like fields should use text storage in MVP with application validation and database checks where practical
- boolean-style flags should use one engine-native boolean representation consistently across all ingest tables
- text payload fields such as `title`, `sanitized_text`, `raw_payload`, `error_detail`, and `error_summary` should use unconstrained text-capable column types unless a proven engine-specific limit is required

This document locks the logical meaning of fields first.
The exact SQL type names may vary slightly by engine.

Timestamp direction for the first migration:

- all stored timestamps should be UTC
- all timestamps should use the same physical database type and precision across ingest tables
- `published_at` may be null when the source value is missing or unparseable, but when stored it must use the same UTC-normalized representation as other ingest timestamps

---

## 4. Logical Tables

### 4.1 `source_item`

Purpose:

- durable canonical source-derived identity and metadata

Primary key:

- `source_item_id`

Logical columns:

- `source_item_id`: required, surrogate primary key
- `source_id`: required, config-owned source identifier
- `source_item_guid`: nullable, original feed item identifier when present
- `canonical_url`: nullable, normalized item URL when available
- `title`: required, normalized display title
- `published_at`: nullable, source-provided published timestamp when parseable
- `fetched_at`: required, ingest fetch timestamp for the inserted item record
- `ingest_dedup_key`: required, deterministic dedup key used for first-ingest identity control
- `dedup_rule`: required, dedup rule that produced `ingest_dedup_key`
- `ingest_status`: required, ingest-level item status

Nullability rules:

- `title`, `source_id`, `fetched_at`, `ingest_dedup_key`, `dedup_rule`, and `ingest_status` must be non-null
- `source_item_guid`, `canonical_url`, and `published_at` may be null

Status direction:

- MVP minimum allowed `ingest_status` value: `ingested`
- future expansion is allowed only by contract update, not silent implementation drift

Uniqueness direction:

- `ingest_dedup_key` should be unique within `source_item`
- `source_item_guid` is not globally unique and must not be treated as the only identity key
- `canonical_url` is not guaranteed unique across all sources and must not be treated as the only identity key

Important rules:

- `source_item` must not contain the sanitized working body
- `source_item` must not contain raw retained payload fields

### 4.2 `source_item_text`

Purpose:

- durable sanitized working text and sanitization outcome data

Primary key:

- `source_item_text_id`

Foreign key direction:

- `source_item_id` references `source_item(source_item_id)`

Logical columns:

- `source_item_text_id`: required, surrogate primary key
- `source_item_id`: required, stable link to the source item
- `sanitized_text`: required, cleaned downstream working text
- `sanitization_method`: required, sanitizer profile, pipeline version, or equivalent stable method label
- `html_detected`: required, boolean-style flag
- `was_truncated`: required, boolean-style flag
- `text_processing_status`: required, text-processing outcome status (`completed`, `low_context`, or `failed`)
- `text_processing_reason`: nullable, compact reason code when status is `low_context` or `failed`
- `raw_text_length`: nullable, measured raw candidate length when available
- `sanitized_text_length`: required, measured sanitized text length
- `reduction_ratio`: nullable, derived reduction metric when available
- `created_at`: required, record creation timestamp
- `updated_at`: required, latest overwrite timestamp for the current MVP record

Nullability rules:

- `source_item_id`, `sanitized_text`, `sanitization_method`, `html_detected`, `was_truncated`, `text_processing_status`, `sanitized_text_length`, `created_at`, and `updated_at` must be non-null
- `text_processing_reason`, `raw_text_length`, and `reduction_ratio` may be null

Reason-code direction:

- prefer stable reason codes as defined in `SANITIZATION_STRATEGY.md` Section 8.3, grouped by `text_processing_status` family

Cardinality direction:

- MVP: exactly zero or one current `source_item_text` record per `source_item`
- if the item is persisted successfully with a sanitization outcome, the preferred direction is to create this record even when the text is low-context
- future multi-version text history must not be introduced implicitly; it requires a new contract

Uniqueness direction:

- `source_item_id` should be unique in `source_item_text` for MVP

Important rules:

- `classify` should read sanitized downstream text from this table, not from `source_item`
- raw cleanup must never target this table

### 4.3 `source_item_raw`

Purpose:

- retention-governed raw evidence for debugging, sanitizer validation, and investigation

Primary key:

- `source_item_raw_id`

Foreign key direction:

- `source_item_id` references `source_item(source_item_id)`

Logical columns:

- `source_item_raw_id`: required, surrogate primary key
- `source_item_id`: required, stable link to the source item
- `raw_payload`: required, retained raw text or fragment
- `retention_class`: required, retention policy class applied at insert time
- `expires_at`: nullable, scheduled expiration timestamp when known
- `created_at`: required, record creation timestamp

Nullability rules:

- `source_item_id`, `raw_payload`, `retention_class`, and `created_at` must be non-null
- `expires_at` may be null

Cardinality direction:

- one `source_item` may have zero or more `source_item_raw` records

Important rules:

- this table exists only for retention-governed raw evidence
- MVP direction is one retained raw text payload per item when raw retention is enabled
- `retention_class` should stay minimal in MVP and does not require an internal exception workflow
- no downstream module should depend on indefinite retention of these rows
- cleanup must delete rows here without deleting the linked `source_item` or `source_item_text`

### 4.4 `source_state`

Purpose:

- current mutable health and fetch state for each source

Primary key:

- `source_id`

Logical columns:

- `source_id`: required, config-owned source identifier and primary key
- `last_fetch_at`: nullable, latest fetch attempt time
- `last_success_at`: nullable, latest successful fetch time
- `last_http_status`: nullable, latest HTTP status when available
- `etag`: nullable, HTTP cache validator
- `last_modified`: nullable, HTTP cache validator
- `consecutive_failures`: required, integer counter
- `last_error_class`: nullable, latest source-level error class
- `last_error_at`: nullable, latest source-level failure time
- `health_status`: required, current source health state
- `quarantine_until`: nullable, quarantine release time when applicable

Nullability rules:

- `source_id`, `consecutive_failures`, and `health_status` must be non-null
- the remaining fields may be null before first successful or failed activity

Status direction:

- minimum supported `health_status` values: `healthy`, `degraded`, `quarantined`

Important rules:

- `source_state` is mutable operational state, not immutable history
- item-level low-context content must not by itself be treated as a source health failure

### 4.5 `fetch_run`

Purpose:

- run-level execution history for ingest

Primary key:

- `fetch_run_id`

Logical columns:

- `fetch_run_id`: required, surrogate primary key
- `started_at`: required, run start timestamp
- `ended_at`: nullable, run end timestamp
- `run_scope`: required, serialized or normalized representation of target scope
- `trigger_type`: required, execution trigger class
- `run_status`: required, final or current run status
- `due_source_count`: required, count of sources targeted at run start
- `attempted_source_count`: required, count of attempted sources
- `succeeded_source_count`: required, count of succeeded sources
- `failed_source_count`: required, count of failed sources
- `error_summary`: nullable, compact run-level error summary

Nullability rules:

- all count fields, `started_at`, `run_scope`, `trigger_type`, `run_status` must be non-null
- `ended_at` and `error_summary` may be null

Status direction:

- minimum supported `trigger_type` values: `scheduled`, `manual`, `recovery`
- minimum supported `run_status` values: `running`, `success`, `partial_failure`, `failed`

### 4.6 `fetch_attempt`

Purpose:

- source-level execution outcome within one fetch run

Primary key:

- `fetch_attempt_id`

Foreign key direction:

- `fetch_run_id` references `fetch_run(fetch_run_id)`

Logical columns:

- `fetch_attempt_id`: required, surrogate primary key
- `fetch_run_id`: required, link to run history
- `source_id`: required, config-owned source identifier
- `started_at`: required, attempt start timestamp
- `ended_at`: nullable, attempt end timestamp
- `retry_count`: required, bounded retry count applied
- `http_status`: nullable, final HTTP status when available
- `error_class`: nullable, source-level error class when failed
- `error_detail`: nullable, failure detail or debug note
- `outcome`: required, source-level outcome
- `new_item_count`: required, count of new inserted items
- `dedup_matched_count`: required, count of dedup matches
- `low_context_count`: required, count of newly inserted items marked low-context
- `sanitization_failure_count`: required, count of item-level sanitization failures tracked as engineering-quality signals
- `normalization_failure_count`: required, count of item-level parse or normalization failures tracked as engineering-quality signals

Nullability rules:

- `fetch_run_id`, `source_id`, `started_at`, `retry_count`, `outcome`, and all count fields must be non-null
- `ended_at`, `http_status`, `error_class`, and `error_detail` may be null

Uniqueness direction:

- `(fetch_run_id, source_id)` should be unique to guarantee one final source outcome row per run

Status direction:

- minimum supported `outcome` values: `success`, `failed`

Important rules:

- item anomaly counts belong here, not in `source_state`
- a source-level success may still have non-zero `low_context_count` or `sanitization_failure_count`
- `sanitization_failure_count` and `normalization_failure_count` are engineering observability signals, not workflow-state fields
- those counts reflect source structure, parser robustness, and rule coverage jointly, so they are most useful for trend analysis rather than strict source ranking

### 4.7 `ingest_dedup_marker`

Purpose:

- explicit dedup state for deterministic ingest identity control

Primary key:

- `dedup_marker_id`

Foreign key direction:

- `source_item_id` references `source_item(source_item_id)`

Logical columns:

- `dedup_marker_id`: required, surrogate primary key
- `dedup_key`: required, deterministic lookup key
- `dedup_rule`: required, dedup rule that produced the key
- `source_item_id`: required, linked source item
- `created_at`: required, record creation timestamp

Nullability rules:

- all columns are non-null

Uniqueness direction:

- `dedup_key` must be unique
- `source_item_id` is not unique: one inserted item holds one primary identity marker (`guid`, `url`, `tp`, or `fh`) plus any number of additional global markers (for example `th` title-hash)

Important rules:

- this table is the fast lookup path for ingest dedup decisions
- an item is treated as a duplicate when any of its keys (primary or additional markers) matches an existing `dedup_key`
- allowed `dedup_rule` values: `guid`, `url`, `tp`, `fh` (primary identity rules) and `th` (additional global title-hash marker)
- dedup auditability must not depend on reconstructing identity from nullable source fields alone

---

## 5. Relationship Summary

- one `source_item` has zero or one `source_item_text` in MVP
- one `source_item` has zero or more `source_item_raw`
- one `source_item` has one or more `ingest_dedup_marker` records (one primary identity key plus optional additional global markers such as `th`)
- one `fetch_run` has many `fetch_attempt`
- one `source_id` has one mutable `source_state`

Delete and retention direction:

- deleting a `fetch_run` may cascade to `fetch_attempt` if operational cleanup ever needs it
- deleting expired `source_item_raw` rows must not cascade to `source_item` or `source_item_text`
- `source_item` and `source_item_text` are durable canonical records and must not be tied to raw-retention cleanup

Foreign-key action direction for the first migration:

- deleting `source_item` must not be part of normal MVP operations
- `source_item_text.source_item_id -> source_item(source_item_id)` should use a restrictive delete action so canonical text cannot be orphaned silently
- `source_item_raw.source_item_id -> source_item(source_item_id)` should use a restrictive delete action; raw retention is handled by deleting raw rows directly, not by deleting the parent item
- `ingest_dedup_marker.source_item_id -> source_item(source_item_id)` should use a restrictive delete action so dedup state is not removed implicitly during ad hoc data changes
- `fetch_attempt.fetch_run_id -> fetch_run(fetch_run_id)` may use cascade delete because attempt rows are pure child execution history

---

## 6. Query And Read Path Direction

Default ingest write path should support:

- fast dedup lookup by `dedup_key`
- source history lookup by `source_id`
- run inspection by `fetch_run_id`
- cleanup selection by retention fields on `source_item_raw`

Default downstream read path should support:

- `classify` reads `source_item.title`, selected metadata, and `source_item_text.sanitized_text`
- `curate` reads `source_item`, `source_item_text`, classification output, and optionally `source_item_raw`
- no downstream module should need to infer working text from raw payload fields

---

## 7. Index Direction

Minimum recommended indexes:

- unique index on `source_item.ingest_dedup_key`
- index on `source_item.source_id`
- index on `source_item.published_at`
- unique index on `source_item_text.source_item_id`
- index on `source_item_raw.source_item_id`
- index on `source_item_raw.expires_at`
- index on `source_item_raw.retention_class`
- index on `fetch_attempt.fetch_run_id`
- unique index on `fetch_attempt(fetch_run_id, source_id)`
- unique index on `ingest_dedup_marker.dedup_key`
- index on `ingest_dedup_marker.source_item_id`

Add more indexes only after query evidence justifies them.

---

## 8. Contract Rules For Future DDL

When this logical design is translated into SQL migrations:

- database checks should enforce bounded enum fields where practical
- foreign keys should protect canonical relationships without blocking raw-retention cleanup
- boolean-style flags may use engine-appropriate representations, but their semantic meaning must remain explicit
- timestamp precision and formatting should be normalized consistently across all ingest tables

Minimum bounded-value sets for first-migration checks:

- `source_item.ingest_status`: `ingested`
- `source_state.health_status`: `healthy`, `degraded`, `quarantined`
- `fetch_run.trigger_type`: `scheduled`, `manual`, `recovery`
- `fetch_run.run_status`: `running`, `success`, `partial_failure`, `failed`
- `fetch_attempt.outcome`: `success`, `failed`
- `source_item_text.text_processing_status`: `completed`, `low_context`, `failed`
- `source_item_text.text_processing_reason`: nullable, but when present should be limited to the reason-code set defined in `SANITIZATION_STRATEGY.md`

First-migration scope direction:

- the first migration should create only the tables required for fetch, sanitization, persistence, source health tracking, run tracking, and dedup lookup
- the first migration should include the indexes listed in this document unless the chosen engine provides an equivalent primary-key or unique-index structure automatically
- cleanup-specific audit tables are not part of the first migration
- cleanup execution is intentionally deferred until operational data exists to justify retention windows, delete batch behavior, and audit shape

The DDL must preserve the separation defined here even if final physical column names change slightly.

---

## 9. Decisions Locked By This Schema

- `source_item` and `source_item_text` are separate canonical records
- `source_item_raw` is retention-governed evidence, not the default downstream text store
- item-level anomaly counts are first-class fetch-attempt outputs
- cleanup must be able to remove raw rows without damaging durable canonical ingest records
- `classify` reads sanitized text from `source_item_text`, not from an ambiguous summary field
