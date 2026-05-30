# Ingest Fetch Execution

**Document version:** v0.1  
**Updated:** 2026-05-28  
**Status:** Draft

---

## 1. Purpose

Define how ingest selects due sources and executes fetch runs with bounded concurrency and stable failure isolation.

---

## 2. Execution Flow

```text
load config
  -> validate config
  -> resolve due sources
  -> shard by fetch_group
  -> fetch with bounded concurrency
  -> parse + normalize entries
  -> compute dedup keys
  -> persist items + source state + run records
  -> emit run summary
```

---

## 3. Due-Source Resolution

Due logic is based on `schedule_class` and prior successful fetch timestamp.

MVP policy:

- skip sources where `enabled=false`
- allow manual override to force run a source
- keep due calculation deterministic for repeatability

---

## 4. Sharding And Concurrency

- `fetch_group` defines deterministic execution shards.
- A run may target all shards or a subset.
- Global concurrency must be bounded by config/runtime parameter.
- Failure in one source must not cancel remaining sources by default.

Recommended run-level metrics:

- total due sources
- attempted/succeeded/failed counts
- per-shard duration

---

## 5. HTTP Behavior

Required behaviors:

- request timeout enforcement
- retry with bounded attempts
- backoff between retries
- response status capture for source health logic

MVP retry defaults:

- retry up to 2 additional attempts for `network_error`, `timeout_error`, and `http_error_5xx`
- do not retry `http_error_4xx`
- do not retry `parse_error`

Cache headers:

- persist and reuse `ETag` if provided
- persist and reuse `Last-Modified` if provided
- handle `304 Not Modified` as successful poll with no new entries

Failure isolation rule:

- source-level failures should not cancel remaining sources in the same run
- run-level failures such as `validation_error`, `persistence_error`, or `unexpected_error` should fail the run

---

## 6. Parsing And Normalization

Normalization goals:

- deterministic mapping from raw feed to internal fields
- preserve enough raw material for audit/debug
- keep nullability explicit (do not silently invent content)

At minimum normalize:

- title
- link/canonical URL
- published timestamp (if present)
- summary/description (if present)
- source attribution metadata

---

## 7. Persistence Guarantees

Each run should persist:

- run-level summary record
- source-level attempt record
- source state updates
- newly ingested items

Write policy must prioritize idempotency:

- repeated run on unchanged feed should not create duplicate logical items

---

## 8. Non-Goals (MVP)

- full article body extraction
- headless browser fallback
- source-specific plugin framework
- LLM-based cleanup/enrichment
