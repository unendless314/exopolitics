# Ingest Error Policy

**Document version:** v0.1  
**Updated:** 2026-05-28  
**Status:** Draft

---

## 1. Purpose

Define error handling expectations for ingest runs and source health governance.

---

## 2. Error Taxonomy (MVP)

Classify ingest errors into stable categories:

- `network_error`
- `timeout_error`
- `http_error_4xx`
- `http_error_5xx`
- `parse_error`
- `validation_error`
- `persistence_error`
- `unexpected_error`

Every failed attempt should record one primary error class and a raw detail payload.

MVP policy keeps this taxonomy stable and intentionally does not further split HTTP or parser subtypes.
Additional diagnosis can rely on `last_http_status` and raw detail payload.

---

## 3. Source Health Signals

Maintain source-level health fields:

- `consecutive_failures`
- `last_error_code`
- `last_error_at`
- `last_success_at`
- `health_status` (example: `healthy`, `degraded`, `quarantined`)
- `quarantine_until` (nullable)

Suggested transition direction:

- transient failures increase counters but keep source active
- repeated failures move source to `degraded`
- persistent failures trigger temporary quarantine
- any successful poll resets failure counters

MVP thresholds:

- 1-2 consecutive failures: keep current source active
- 3-4 consecutive failures: set `health_status=degraded`
- 5 or more consecutive failures: set `health_status=quarantined`

Successful poll behavior:

- set `consecutive_failures=0`
- set `health_status=healthy`
- clear `quarantine_until` when applicable
- update `last_success_at`

`304 Not Modified` counts as a successful poll.

MVP does not introduce a separate source-attempt `partial_success` outcome.
Each source attempt is recorded as either `success` or `failed`.

---

## 4. Error Handling Scope

Default rule:

- source-level failures must not cancel remaining sources in the same run

Source-level errors:

- `network_error`
- `timeout_error`
- `http_error_4xx`
- `http_error_5xx`
- `parse_error`

Run-level errors:

- `validation_error`
- `persistence_error`
- `unexpected_error`

Run-level errors should fail fast because they indicate invalid input contracts, unreliable persistence, or unknown system behavior.

---

## 5. Retry Policy

MVP retry behavior:

- `network_error`: retry up to 2 additional attempts
- `timeout_error`: retry up to 2 additional attempts
- `http_error_5xx`: retry up to 2 additional attempts
- `http_error_4xx`: do not retry
- `parse_error`: do not retry
- run-level errors: do not retry; fail the run

Retry backoff should remain bounded and simple.

---

## 6. Quarantine And Disable Rules

MVP should separate:

- temporary runtime quarantine (automatic)
- config-level disable (`enabled=false`, manual)

Rules:

- quarantine is reversible by time window or manual action
- disable requires explicit config change
- quarantined/disabled sources still appear in operational reports
- automatic quarantine duration defaults to 24 hours
- when `quarantine_until` expires, the source becomes eligible for fetch again

---

## 7. Observability Minimum

Each run should emit:

- run ID
- started/ended timestamps
- attempted/succeeded/failed source counts
- top error classes
- list of quarantined sources (if any)

Run status should be one of:

- `success`
- `partial_failure`
- `failed`

Suggested interpretation:

- `success`: no source failures and no run-level failure
- `partial_failure`: one or more source failures, but run completed
- `failed`: run terminated by a run-level error

This module does not require full monitoring stack in MVP, but it must produce actionable logs/records.

---

## 8. Escalation Guidance

Operator escalation is recommended when:

- a high-priority source repeatedly fails across multiple scheduled runs
- persistence errors occur (risking data loss)
- parse failure rate spikes after schema/provider changes

Escalation handling steps are defined in `OPERATIONS_RUNBOOK.md`.
