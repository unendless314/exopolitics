# Cleanup Execution

**Status:** Active rewrite draft  
**Updated:** 2026-06-07

---

## 1. Purpose

This document defines how `ingest` cleanup removes expired raw retained records without damaging the downstream working contract.

Cleanup is part of ingest operations, but it is not part of the normal fetch execution path.

---

## 2. Scope

Cleanup owns:

- selecting raw retained records eligible for deletion under retention policy
- skipping records protected by exception retention or operator freeze
- deleting expired raw retained records
- recording cleanup outcomes for auditability

Cleanup does not own:

- fetching new source payloads
- sanitization of new feed items
- classification or review logic
- deletion of durable sanitized working text

---

## 3. Expected Flow

```text
load retention policy
  -> identify raw retained records eligible for cleanup
  -> exclude exception-retained or frozen records
  -> delete eligible raw records
  -> record cleanup counts and audit information
```

---

## 4. Configuration Direction

Cleanup configuration should be module-level policy, not per-source fetch configuration.

Recommended direction:

- keep source-specific fetch and sanitization settings in source config
- keep retention windows, cleanup run controls, and audit behavior in module-level cleanup or retention config

This is because cleanup is an ingest operational path, not a per-source fetch behavior.

Recommended config shape:

```yaml
raw_retention:
  default_days: 14
  delete_batch_size: 500
  dry_run: false
  audit_log: true
  exception_classes:
    - investigation
    - operator_frozen
```

Example of a record-level retention override direction:

```yaml
raw_retention_override:
  retention_class: investigation
  expires_at: 2026-07-01T00:00:00Z
```

This document does not lock final file names yet.
It locks the policy shape that cleanup implementation should follow.

---

## 5. Invariants

Cleanup must preserve the following invariants:

- deleting raw retained records must not delete normalized source items
- deleting raw retained records must not delete sanitized working text
- cleanup must respect exception retention markers
- cleanup must be safe to run repeatedly

---

## 6. Audit Expectations

Cleanup should make it possible to answer:

- when cleanup ran
- how many raw records were eligible
- how many raw records were deleted
- how many records were skipped due to exception retention
- which retention window or retention class was applied

The audit trail does not require permanent storage of the deleted payload itself.

---

## 7. Failure Expectations

- cleanup failure must not corrupt normalized source items or sanitized text records
- partial cleanup should be detectable and auditable
- cleanup should fail safely rather than deleting ambiguous records

---

## 8. Relationship To Fetch Execution

- fetch execution may create raw retained records when policy allows
- cleanup execution removes expired raw retained records later
- these are separate ingest operational paths and should not be conflated

---

## 9. Implementation Staging Direction

Cleanup does not need to block initial ingest rebuild work.

Recommended staging:

1. implement fetch, sanitization, and persistence first
2. capture retention metadata needed for later cleanup
3. observe real data growth and debugging needs
4. implement cleanup execution after retention windows are better informed by actual operation

This keeps cleanup contractually visible now without forcing premature deletion behavior before operational data exists.
