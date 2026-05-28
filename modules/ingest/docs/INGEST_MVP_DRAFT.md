# Ingest MVP Draft

**Document version:** v0.1 draft  
**Updated:** 2026-05-28  
**Status:** Draft

---

## 1. Goal

Define a first-pass implementation target for `ingest` without freezing low-level details too early.

This document is a planning draft, not a final implementation contract.

---

## 2. MVP Objective

The MVP should prove that the system can reliably do the following on a single machine:

- load source definitions from configuration
- validate configuration structure before execution
- select sources that are due to run
- fetch multiple feeds in parallel with bounded concurrency
- parse and normalize returned entries
- avoid obvious duplicate inserts
- persist ingest results and source state
- keep enough history to debug fetch failures later

If the MVP can do those things consistently, later modules can be built on top of stable stored data.

---

## 3. Proposed Execution Flow

```text
load config
  -> validate config
  -> resolve due sources
  -> shard by fetch_group
  -> fetch feeds concurrently
  -> parse feed payloads
  -> normalize entries
  -> deduplicate
  -> persist source items and fetch metadata
  -> emit run summary
```

---

## 4. Proposed Responsibilities

### In scope

- YAML config loading
- source config validation
- due-source selection based on `schedule_class`
- shard-aware concurrent fetching based on `fetch_group`
- feed parsing
- basic normalization into a canonical ingest shape
- deduplication at ingest time
- source health updates
- fetch run and error recording

### Out of scope for MVP

- per-source custom parser plugins unless clearly needed
- browser automation for hostile sites
- article body scraping
- language detection beyond optional metadata capture
- LLM-assisted cleanup or enrichment
- any publish-layer transformation

---

## 5. Suggested Internal Data Areas

Without locking the final schema yet, the ingest module will likely need records for:

- `source`
  - static source definition plus operational metadata
- `fetch_run`
  - one execution run summary
- `fetch_attempt` or equivalent
  - per-source fetch outcome
- `source_item`
  - normalized feed entries from remote sources

This is still a conceptual split, not a final schema decision.

---

## 6. Validation Expectations

At minimum, config validation should check:

- each source has a unique `id`
- each source has a valid `xml_url`
- each `category_id` exists in category config
- each `fetch_group` is within the configured shard range
- each `schedule_class` exists
- disabled categories or disabled sources behave predictably

The validator should fail fast on structural errors.

---

## 7. Concurrency Model

The current planning assumption is:

- `fetch_group` is a deterministic shard key
- each run may execute one or more shards
- parallelism should be bounded globally
- failure in one shard should not invalidate results from other shards

This keeps the model simple enough for a personal site while preserving room for future growth.

---

## 8. Dedup Strategy Direction

The dedup rules are not final, but the MVP should at least prepare for layered matching, such as:

- stable feed GUID if trustworthy
- normalized final URL
- title plus published timestamp heuristic
- source-scoped fallback hash

The exact ordering and conflict policy should be defined later with real feed samples.

---

## 9. Operational Expectations

The module should be designed for routine unattended runs.

That implies basic support for:

- timeouts
- retry limits
- error capture
- run summaries
- observable source health

The MVP does not need advanced observability infrastructure, but it should not be opaque.

---

## 10. Tech Direction

This draft remains aligned with the current system-level recommendation:

- implementation language: Python 3.11+
- HTTP client: `aiohttp`
- feed parsing: `feedparser`
- canonical DB: SQLite for MVP

The module should avoid unnecessary tight coupling to SQLite-only behavior so that future migration remains possible.

---

## 11. Directory Direction

Planned module layout for now:

```text
modules/
  ingest/
    docs/
      README.md
      INGEST_MVP_DRAFT.md
    config/
    src/
    tests/
```

Only `docs/` is being established in this step.

---

## 12. Deferred Questions

These items are intentionally deferred until the architecture conversation settles further:

- final database schema
- exact scheduler invocation shape
- retention window for raw payloads
- source disable/quarantine policy
- whether source config should support per-source overrides for timeout, headers, or retry
- whether `html_url` should normalize to `null` instead of empty string
- whether some feeds need source-specific normalization rules from day one
