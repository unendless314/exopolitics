# Ingest Module Implementation Plan Revised

**Status:** Recommended implementation plan  
**Updated:** 2026-06-09  
**Target Module:** `modules/ingest/`

---

## 1. Purpose

This document provides a revised implementation plan for the rewritten `ingest` module.

It is intended to be practical for engineering execution while preserving a clear distinction between:

- contract-locked requirements already defined by active docs
- MVP implementation choices that are acceptable now but may change after real-world validation

This distinction is important because the rewrite docs already lock the module boundary, storage separation, and first-migration scope, but they do not lock every internal algorithm choice.

---

## 2. Planning Rules

- do not treat an MVP implementation choice as a permanent contract unless an active module doc already locks it
- prefer the smallest implementation that fully satisfies the current ingest docs
- implement fetch, sanitization, persistence, and validation first
- defer cleanup execution, cleanup scheduling, and cleanup audit-table design until operational data exists
- keep implementation choices explicit so they can be tuned later without rewriting top-level contracts

---

## 3. Contract-Locked Requirements

The following requirements are already locked by active documentation and must be followed during implementation.

### 3.1 Module boundary

- `ingest` owns source configuration interpretation, fetching, normalization, deduplication, sanitization, raw-retention handling, source health tracking, and fetch execution records
- `ingest` does not own classification, curate, publish formatting, or site rendering

### 3.2 Canonical storage separation

- `source_item` stores durable source-derived identity and metadata
- `source_item_text` stores sanitized working text and sanitization outcomes
- `source_item_raw` stores retention-governed raw evidence only
- downstream working text must come from `source_item_text`, not from raw payload fields

### 3.3 First migration scope

The first migration must cover only the ingest data needed for:

- config-backed source execution
- fetch run and fetch attempt recording
- source state tracking
- source item persistence
- sanitized working text persistence
- raw retained record persistence
- dedup lookup and dedup state

The first migration must not include:

- cleanup audit tables
- cleanup execution tables beyond the retention metadata already defined on `source_item_raw`
- classify, curate, publish, or site tables

### 3.4 Storage rules that must be honored

- primary keys, foreign-key direction, uniqueness rules, and minimum indexes must follow `STORAGE_SCHEMA.md`
- first-migration foreign-key delete behavior must follow the first-migration direction in `STORAGE_SCHEMA.md`
- bounded enum-like values should be constrained where practical in the chosen engine
- `source_item_text.low_context_reason` must use only the documented reason-code set
- all ingest timestamps must use one consistent UTC-normalized representation

### 3.5 Execution rules that must be honored

- one source failure must not abort unrelated sources by default
- one source attempt should have a clear transaction boundary for persistence
- source-level success and item-level usefulness are related but not identical
- low-context is not the same as sanitization failure
- cleanup is deferred; current implementation only needs to write retention metadata required for later cleanup

---

## 4. Accepted MVP Implementation Choices

The following choices are acceptable for the first implementation even though they are not permanent cross-version contracts.

### 4.1 HTML and selector handling

- use `beautifulsoup4` as the HTML parsing dependency for the ingest module
- use it to support `content_selectors`, `remove_selectors`, and tag-level removal for known non-content elements such as `script`, `style`, `nav`, and `footer`
- do not add additional HTML-cleaning frameworks unless a concrete gap is discovered

### 4.2 CLI entry point

- use `python -m modules.ingest.src.cli` as the initial execution entry point

### 4.3 SQLite runtime choices

- use Python `sqlite3`
- enable `PRAGMA foreign_keys = ON`
- use `sqlite3.Row` or equivalent row mapping for repository reads
- use a transaction helper for per-source persistence boundaries
- `BEGIN IMMEDIATE` is acceptable as the default transaction strategy for MVP

### 4.4 Migration infrastructure

- use a small migration runner that records applied migrations in a `schema_migrations` metadata table
- keep migration naming and execution simple and deterministic

### 4.5 Fetch runtime choices

- use `httpx.AsyncClient` for HTTP requests
- use bounded concurrency such as `asyncio.Semaphore`
- support ETag and Last-Modified validators when available

---

## 5. Provisional MVP Policies

The following policies are reasonable for MVP and may be implemented now, but they must be treated as implementation choices rather than permanent contracts.

### 5.1 Dedup strategy

Accepted MVP direction:

1. use GUID when present and usable
2. otherwise use canonical URL when present and usable
3. otherwise use normalized title plus `published_at`
4. otherwise use a fallback hash from normalized item inputs

Requirements that still apply:

- the dedup key must be deterministic
- the chosen rule must be recorded in `dedup_rule`
- the implementation must not assume nullable source fields are always present or globally trustworthy

Important note:

- this precedence order is an MVP ingest strategy, not a locked cross-module contract

### 5.2 Retry strategy

Accepted MVP direction:

- retry transient network errors up to 2 times after the initial attempt
- retry HTTP 5xx responses up to 2 times after the initial attempt
- do not retry most HTTP 4xx responses by default
- always record the final outcome in `fetch_attempt`

Important note:

- this retry behavior is an operational default for MVP, not a locked long-term contract

### 5.3 Low-context thresholds

Accepted MVP direction:

- implement low-context checks using fixed rule-based thresholds
- keep threshold constants centralized so they can be tuned after observing real feeds
- avoid spreading threshold values across multiple modules or repository layers

---

## 6. Phase Order

### Phase 1: Config Loader And Validation

Implement:

- YAML loading for `sources.yaml`, `categories.yaml`, and `retention_policy.yaml`
- typed config models
- strict validation of required fields, types, ranges, and references
- deterministic merge of `sanitization_profile` plus `sanitization_overrides`
- descriptive validation errors that identify filename, field, and source ID when applicable

Deliverable:

- a usable config loader that fails before network or database work on invalid configuration

### Phase 2: Storage And Migrations

Implement:

- first migration for the seven ingest logical tables
- required indexes and bounded-value checks
- foreign-key actions consistent with `STORAGE_SCHEMA.md`
- migration runner and `schema_migrations` table
- connection factory and transaction helper

Deliverable:

- a working SQLite schema and migration path suitable for fresh rebuilds

### Phase 3: Sanitization Pipeline

Implement:

- preferred input field selection
- HTML detection and parsing
- selector-based extraction and removal
- entity decoding and whitespace normalization
- length capping and truncation flags
- metrics calculation
- low-context detection and reason-code assignment

Deliverable:

- a deterministic sanitization function that produces `source_item_text` records and associated metrics

### Phase 4: Fetch Execution And Persistence

Implement:

- due-source selection
- bounded-concurrency fetching
- validator-aware HTTP requests using ETag and Last-Modified when known
- feed parsing with `feedparser`
- normalization into ingest item inputs
- dedup lookup and insert logic
- source-item, text, raw, state, run, and attempt persistence
- source-level transaction boundaries

Deliverable:

- a working ingest fetch path that can fetch real feeds and write canonical ingest records

### Phase 5: Minimal CLI

Implement only:

- `validate`
- `migrate`
- `fetch`

Optional flags may be added only when they directly support current ingest docs and the initial fetch workflow.

Deliverable:

- a minimal command surface sufficient for local operation and testing

### Phase 6: Tests And Validation

Implement:

- config validation tests
- sanitization pipeline tests
- dedup tests
- migration and repository tests
- orchestrator integration tests with local fixtures
- limited real-feed validation after the core pipeline is stable

Deliverable:

- test coverage for the most important correctness and contract behaviors

---

## 7. Explicitly Deferred Work

The following work should not be part of the first implementation pass:

- cleanup execution
- cleanup scheduling
- cleanup audit tables
- `show-health` CLI command
- `export-report` CLI command
- static HTML side-by-side report generation
- JSON output modes that are not required for MVP operation
- classify, curate, publish, or site functionality

These items may become useful later, but they should not slow or blur the first ingest rebuild.

---

## 8. Recommended Engineering Breakdown

### Epic 1: Config And Validation

- define config models
- implement YAML loading
- implement strict validation
- implement cross-file reference checks
- implement sanitization override merge logic

### Epic 2: Storage And Repositories

- write first migration
- build migration runner
- implement connection and transaction helpers
- implement repositories for source state, source items, text, raw, fetch runs, fetch attempts, and dedup markers

### Epic 3: Sanitization

- implement raw-field selection
- implement HTML parsing and selector extraction
- implement whitespace normalization and truncation
- implement metrics and low-context checks

### Epic 4: Fetch Orchestration

- implement due-source resolution
- implement async fetcher with bounded concurrency
- implement feed parsing and normalization
- implement dedup strategy
- implement per-source persistence flow
- update run and source-state records

### Epic 5: Minimal CLI

- implement `validate`
- implement `migrate`
- implement `fetch`

### Epic 6: Tests

- unit tests for config, sanitization, and dedup
- repository and migration tests
- integration tests for fetch orchestration using fixtures

---

## 9. Decision Escalation Rules

If implementation work encounters a question not locked by active docs, prefer the following order:

1. choose the smallest implementation that satisfies the existing contracts
2. keep the choice isolated and easy to change later
3. ask one narrow question only if the issue would materially change persisted semantics or public CLI behavior

Examples of questions that may justify escalation:

- changing dedup rule precedence after initial validation
- introducing new persisted status values beyond the documented minimum sets
- expanding CLI surface beyond `validate`, `migrate`, and `fetch`
- adding cleanup-related tables or processes during MVP

---

## 10. Immediate Next Step

Start with Phase 1 and Phase 2 together:

- config loader and strict validation
- first migration and migration runner

Those pieces create the foundation for the sanitization and fetch pipeline without overcommitting to later operational tooling.
