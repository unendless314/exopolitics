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
- successful fetch resets or reduces failure counters

---

## 4. Quarantine And Disable Rules

MVP should separate:

- temporary runtime quarantine (automatic)
- config-level disable (`enabled=false`, manual)

Rules:

- quarantine is reversible by time window or manual action
- disable requires explicit config change
- quarantined/disabled sources still appear in operational reports

---

## 5. Observability Minimum

Each run should emit:

- run ID
- started/ended timestamps
- attempted/succeeded/failed source counts
- top error classes
- list of quarantined sources (if any)

This module does not require full monitoring stack in MVP, but it must produce actionable logs/records.

---

## 6. Escalation Guidance

Operator escalation is recommended when:

- a high-priority source repeatedly fails across multiple scheduled runs
- persistence errors occur (risking data loss)
- parse failure rate spikes after schema/provider changes

Escalation handling steps are defined in `OPERATIONS_RUNBOOK.md`.
