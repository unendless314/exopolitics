# Ingest Operations Runbook

**Document version:** v0.2  
**Updated:** 2026-06-02  
**Status:** Active

---

## 1. Purpose

Provide practical run and recovery guidance for operating ingest in daily scheduled mode.

---

## 2. CLI Reference

All operations go through the ingest CLI, executed from the repository root:

```text
python -m modules.ingest.src.cli <subcommand> [flags]
```

Subcommands:

- `validate`      — load `modules/ingest/config/*.yaml` and check schema rules (no network, no DB)
- `migrate`       — apply SQLite DDL migrations to the canonical database
- `fetch`         — execute a fetch run (auto-applies pending migrations first)
- `show-health`   — report current per-source health state
- `export-report` — export an interactive HTML dashboard of the ingested database

Common flags on `fetch`:

- `--groups <ids...>`        restrict to specific `fetch_group` shards
- `--source-ids <ids...>`    restrict to specific source IDs
- `--force`                  bypass schedule-due check and active quarantine (still respects `enabled=false`)
- `--dry-run`                list eligible sources without any HTTP or DB writes
- `--trigger-type {scheduled|manual|recovery}`  recorded on the `fetch_run` row for auditability
- `--json`                   emit the run summary in JSON (use this for cron/scheduled mode)

Common flags on `export-report`:

- `--out <path>`             where the HTML report should be saved (default: `data/ingested_report.html`)
- `--limit <number>`         maximum number of latest articles to export (default: 500)

Common flag on every DB-touching subcommand:

- `--db-path <path>`         override the default SQLite location (default: `data/canonical.db` at the repo root)

---

## 3. Standard Operating Checks

Before scheduled deployment or major config updates:

1. **Validate config structure**
   ```text
   python -m modules.ingest.src.cli validate
   ```
   Exit code `0` with zero `ERROR:` lines is required. `WARNING:` lines are non-blocking.

2. **Run a limited-scope dry-run to confirm sharding is correct**
   ```text
   python -m modules.ingest.src.cli fetch --dry-run --groups <id>
   ```

3. **Execute a limited-scope real fetch on a known-good shard**
   ```text
   python -m modules.ingest.src.cli fetch --groups <id> --trigger-type manual
   ```

4. **Verify run summary and source health records**
   ```text
   python -m modules.ingest.src.cli show-health
   ```
   Check that no expected-healthy source has slipped to `degraded` or `quarantined`.

---

## 4. Common Failure Playbooks

### A. Single source repeatedly failing

1. inspect last error class and HTTP status via `show-health`:
   ```text
   python -m modules.ingest.src.cli show-health --json
   ```
2. verify source `xml_url` and feed availability manually
3. confirm whether the source has moved to `degraded` or `quarantined`
4. open follow-up issue for source-specific handling if needed

Health transition thresholds (consistent with `ERROR_POLICY.md` §3 and `scheduler.apply_fetch_failure`):

- 1–2 consecutive failures: `healthy`
- 3–4 consecutive failures: `degraded`
- 5 or more consecutive failures: `quarantined` for 24 hours (auto-released when `quarantine_until` elapses)

### B. Many sources failing at once

1. check network/DNS/TLS environment first
2. inspect recent config changes
3. run targeted retry on a small known-good subset:
   ```text
   python -m modules.ingest.src.cli fetch --source-ids <id1> <id2> --force --trigger-type recovery
   ```
4. avoid mass disable until root cause is confirmed

If the run stopped on `validation_error`, `persistence_error`, or `unexpected_error`, treat it as a run-level incident rather than independent source failures.

### C. Unexpected dedup spike or duplicate inserts

1. inspect normalization and dedup-key logic changes
2. compare current run against previous successful run
3. run repair/backfill procedure only after root cause is understood

---

## 5. Recovery Operations

Supported recovery modes:

- **rerun by `fetch_group`** — implemented:
  ```text
  python -m modules.ingest.src.cli fetch --groups <id> --trigger-type recovery
  ```
- **rerun by source ID list** — implemented:
  ```text
  python -m modules.ingest.src.cli fetch --source-ids <id1> <id2> --trigger-type recovery
  ```
- **rerun by time window** — **Deferred** (not implemented in the current CLI; tracked separately, do not assume availability).

`--force` may be combined with the implemented modes to bypass schedule-due checks and active quarantines (still respects `enabled=false`).

Recovery runs must:

- preserve auditability — each run produces a new `fetch_run_id`; always pass `--trigger-type recovery` so the row is distinguishable from scheduled and manual runs
- avoid destructive overwrite of historical attempts — the schema appends to `fetch_attempt`, never updates prior rows
- emit clear summary for later review — capture stdout, or use `--json` for machine-readable archival

---

## 6. Change Safety Checklist

Any change touching config schema, fetch behavior, or dedup policy should include:

1. docs update in `modules/ingest/docs/`
2. test updates in `modules/ingest/tests/`
3. backward-compatibility note
4. rollback path

---

## 7. Ownership And Handoff

`ingest` operators own source intake reliability.  
Downstream modules (`classify`, `review`) should not patch ingest data quality by silent workarounds.

When handoff quality drops below threshold, escalate to ingest module maintenance rather than downstream compensations.
