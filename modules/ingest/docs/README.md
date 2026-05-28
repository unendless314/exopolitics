# Ingest Module Overview

**Document version:** v0.1 draft  
**Updated:** 2026-05-28  
**Status:** Draft

---

## 1. Positioning

`ingest` is the first executable module in the system.

Its job is to:

- read RSS source configuration
- decide which sources should be fetched in the current run
- fetch feeds with bounded parallelism
- normalize feed entries into a stable internal shape
- deduplicate incoming items
- persist source metadata and raw items into canonical storage

`ingest` is intentionally upstream only.

It should not:

- call LLMs
- decide final public visibility
- generate publish-layer output
- render site pages

---

## 2. Ownership

At the current planning stage, `ingest` owns:

- source configuration
- category configuration used by source config validation
- fetch group contract
- schedule class contract
- source health tracking
- raw ingestion records
- deduplication inputs and ingest-time metadata

`ingest` does not own:

- topic classification policy
- review queue policy
- edit authorship
- publish formatting
- front-end presentation

---

## 3. Inputs And Outputs

### Inputs

- RSS/feed source config
- categories config
- current time
- prior source state from canonical storage

### Outputs

- new or updated `source_item` records
- source fetch metadata
- fetch run records
- source health signals
- ingest-time deduplication markers

---

## 4. Boundaries With Other Modules

### Upstream boundary

`ingest` talks to:

- source config files
- remote feed endpoints
- canonical database

### Downstream boundary

`ingest` hands off persisted records to later modules.

Expected downstream consumers:

- `classify` reads newly ingested items
- `review` does not fetch feeds directly
- `publish` does not repair missing ingest records
- `site` never reads remote feeds

---

## 5. Design Principles

- Save first, classify later.
- Keep content semantics separate from fetch scheduling.
- A failed source must not collapse the whole run.
- Publish-facing concerns must not leak into ingest.
- The module should remain small enough to operate on one machine.
- Data written by `ingest` should be reusable by future pipelines without re-fetching.

---

## 6. Current Config Contract

Based on the current draft assets in `config/`, source config already expresses three independent axes:

- `category_id`
  - semantic grouping only
- `fetch_group`
  - execution shard for parallel fetching
- `schedule_class`
  - fetch cadence tier

This separation should be preserved when the files are later moved into `modules/ingest/config/`.

---

## 7. Near-Term Scope

The first implementation pass should focus on:

- config loading and validation
- fetch scheduling selection
- feed retrieval
- normalization
- deduplication
- canonical persistence
- source health bookkeeping

The first pass should not overreach into:

- complex source-specific adapters
- full-text extraction
- LLM-assisted parsing
- edit drafting
- automated deletion policy

---

## 8. Open Decisions

The following are intentionally not finalized yet:

- canonical DB table design
- exact deduplication key strategy
- retry and backoff policy
- timeout defaults
- raw payload retention format
- whether fetch run logs live in the same DB or a sidecar store
- exact CLI shape for scheduled execution

These should be refined after the overall architecture discussion stabilizes.
