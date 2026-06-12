# Ingest Module Docs

This directory contains the active ingest module contracts for the rewrite.

Status:

- these docs replace the pre-reset ingest plan for new implementation work
- prior drafts now live under `modules/ingest/docs/backup/` and `modules_archive/ingest/`
- this set is intended to be implementation-facing, not just directional
- `STORAGE_SCHEMA.md` includes the minimum DDL-facing decisions needed for the first migration; a separate DDL doc is not required for MVP
- cleanup remains a documented later-stage ingest operation and does not block the first rebuild

Read order:

1. `DATA_CONTRACT.md`
2. `SOURCE_CONFIG_CONTRACT.md`
3. `SANITIZATION_STRATEGY.md`
4. `STORAGE_SCHEMA.md`
5. `FETCH_EXECUTION.md`
6. `CLEANUP_EXECUTION.md`
7. `RETENTION_POLICY.md`

Scope note:

- `ingest` owns the transition from raw feed input to sanitized working text
- `ingest` does not own classification, review, publish formatting, or site rendering
- `ingest` does not filter out old/historical items by date; all fetched unique items are stored. Downstream time-filtering responsibilities are governed by the top-level [DATA_LIFECYCLE.md](file:///C:/Users/user/Documents/derived-work/docs/DATA_LIFECYCLE.md#11-temporal-policy-and-historical-data) policy.

