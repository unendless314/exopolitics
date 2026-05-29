# Ingest Operations Runbook

**Document version:** v0.1  
**Updated:** 2026-05-28  
**Status:** Draft

---

## 1. Purpose

Provide practical run and recovery guidance for operating ingest in daily scheduled mode.

---

## 2. Standard Operating Checks

Before scheduled deployment or major config updates:

1. validate config structure
2. run a limited-scope fetch on selected shards
3. verify run summary and source health records
4. confirm no abnormal duplicate spike in `source_item`

Recommended quick checks:

- `rg --files modules/ingest/config`
- `git diff -- modules/ingest/config/ modules/ingest/docs/`

---

## 3. Common Failure Playbooks

### A. Single source repeatedly failing

1. inspect last error class and HTTP status
2. verify source `xml_url` and feed availability manually
3. temporarily quarantine source if failure is persistent
4. open follow-up issue for source-specific handling if needed

### B. Many sources failing at once

1. check network/DNS/TLS environment first
2. inspect recent config changes
3. run targeted retry on a small known-good subset
4. avoid mass disable until root cause is confirmed

### C. Unexpected dedup spike or duplicate inserts

1. inspect normalization and dedup-key logic changes
2. compare current run against previous successful run
3. run repair/backfill procedure only after root cause is understood

---

## 4. Recovery Operations

Supported recovery modes should include:

- rerun by `fetch_group`
- rerun by source ID list
- rerun by time window

Recovery runs must:

- preserve auditability (new run ID)
- avoid destructive overwrite of historical attempts
- emit clear summary for later review

---

## 5. Change Safety Checklist

Any change touching config schema, fetch behavior, or dedup policy should include:

1. docs update in `modules/ingest/docs/`
2. test updates in `modules/ingest/tests/`
3. backward-compatibility note
4. rollback path

---

## 6. Ownership And Handoff

`ingest` operators own source intake reliability.  
Downstream modules (`classify`, `review`) should not patch ingest data quality by silent workarounds.

When handoff quality drops below threshold, escalate to ingest module maintenance rather than downstream compensations.
