# Ingest Storage Schema Lock Checklist

**Document version:** v0.1  
**Updated:** 2026-05-30  
**Status:** Active

---

## 1. Purpose

Provide an implementation-ready checklist so engineers can convert `STORAGE_SCHEMA_DRAFT.md` into executable persistence code with minimal ambiguity.

---

## 2. How To Use

- complete every checkbox before writing production persistence code
- record final decisions directly in this checklist (or linked DDL doc) during implementation
- keep this file in sync with ingest contracts (`DATA_CONTRACT.md`, `DEDUP_POLICY.md`, `ERROR_POLICY.md`)

---

## 3. Schema Lock Checklist

### A. Column Types And Time Format

- [ ] define SQLite type for every field in `source_state`, `fetch_run`, `fetch_attempt`, `source_item`, `ingest_dedup_marker`
- [ ] lock timestamp storage format to UTC ISO-8601 text
- [ ] define integer/text conventions for status and enum-like fields

### B. Nullability

- [ ] produce a complete nullability matrix (`NOT NULL` vs nullable) for all fields
- [ ] explicitly justify nullable fields that affect dedup or health decisions

### C. Enum / Taxonomy Values

- [ ] lock allowed values for `run_status`
- [ ] lock allowed values for `fetch_attempt.outcome`
- [ ] lock allowed values for `health_status`
- [ ] lock allowed values for `ingest_status`
- [ ] lock allowed values for `dedup_rule`
- [ ] confirm `source_state.last_error_class` and `fetch_attempt.error_class` share one taxonomy

### D. Keys, Constraints, And FK Behavior

- [ ] lock primary key strategy for each table
- [ ] lock unique constraint `fetch_attempt(fetch_run_id, source_id)`
- [ ] lock unique constraint `ingest_dedup_marker(dedup_key)`
- [ ] lock foreign key constraints and `ON DELETE/ON UPDATE` behavior
- [ ] ensure config-owned referential rule for `source_id` is enforced at run start validation

### E. Dedup Key Encoding And Determinism

- [ ] lock dedup key prefix rule (`guid:`, `url:`, `tp:`, `fh:`)
- [ ] lock canonicalization rules used before dedup key generation
- [ ] lock `tp:` timestamp precision and normalization behavior
- [ ] lock `fh:` hash input field order and hash algorithm

### F. Write Path And Transaction Boundaries

- [ ] define write order for one source attempt (`source_item` / `ingest_dedup_marker` / `fetch_attempt` / `source_state`)
- [ ] lock transaction boundaries for source-level writes
- [ ] define rollback behavior for partial failures

### G. SQLite DDL And Migration Plan

- [ ] write initial SQLite DDL for all ingest tables and indexes
- [ ] define migration naming/versioning convention
- [ ] define forward-only migration policy and rollback expectations

### H. Index Validation

- [ ] lock baseline index set in DDL
- [ ] define core query set used to validate index effectiveness
- [ ] run sample-volume checks and record any index additions with rationale

---

## 4. Completion Criteria

This checklist is complete when:

- every checkbox is resolved
- initial DDL and first migration are committed
- no unresolved schema ambiguity blocks persistence implementation
