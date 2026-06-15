# Storage And Retention

**Status:** Active rewrite draft  
**Updated:** 2026-06-05

---

## 1. Purpose

This document defines what data should be stored, why it is stored, and how long it should live.

The rewrite makes one core distinction explicit:

- storage purpose
- retention duration

These are related, but they are not the same decision.

---

## 2. Storage Principles

- canonical storage keeps the durable operational record
- raw input retention exists for validation, debugging, and exceptions
- sanitized working text is the long-term downstream representation
- public publish output is derived and rebuildable
- retention policy should control storage growth instead of accidental neglect

---

## 3. Storage Layers

### 3.1 Canonical Long-Term Storage

This layer should retain the records the system needs for ongoing operation and history.

Examples:

- normalized source item identity and metadata
- sanitized working text
- source health state
- fetch execution records
- classification results
- curation decisions
- publish records or publish references
- provenance and disclosure metadata

### 3.2 Raw Short-Retention Storage

This layer exists for:

- sanitizer validation
- source quality inspection
- debugging misclassification or parsing issues
- selected audit or exception cases

Raw storage may live inside canonical storage during early stages, but its retention must still be governed explicitly.

### 3.3 Publish Storage

This layer contains exported approved outputs used by the site.

Properties:

- rebuildable
- human-readable when practical
- not the sole historical system record

---

## 4. What Must Be Stored Long-Term

The rewritten system should keep these categories durably:

- source item identifiers and normalized metadata
- sanitized working text used by downstream modules
- dedup markers or equivalent ingest identity controls
- source fetch state and execution history
- classification outputs
- curation decisions and responsibility trail
- publish-layer references and disclosure metadata

These are the records that define system behavior and editorial history.

---

## 5. What Should Be Short-Retention By Default

The following should be treated as short-retention by default:

- raw feed summary or description payloads
- raw embedded HTML fragments
- oversized noisy text kept only for sanitizer validation

These records are useful, but they do not need permanent default retention.

---

## 6. Recommended Retention Direction

This document does not hard-lock exact durations yet, but it locks the policy shape:

- raw input: bounded retention window
- sanitized working text: long-term retention
- classification and curation outcomes: long-term retention
- publish exports: rebuildable retention layer

Suggested early-production raw retention windows to evaluate:

- 7 days
- 14 days
- 30 days

Final choice should be based on:

- observed debugging needs
- source noisiness
- disk growth
- sanitizer iteration frequency

---

## 7. Exception Retention

Some records may need retention beyond the default raw window.

Examples:

- items tied to a classification anomaly investigation
- items marked for legal, compliance, or dispute reasons
- samples intentionally preserved for sanitizer evaluation
- records manually frozen by operator decision

Default cleanup must support exceptions rather than assuming all raw data lives forever.

---

## 8. Cleanup Policy

Cleanup is a first-class operational rule, not an afterthought.

Expected behavior:

- raw records older than the configured retention window may be deleted
- cleanup must not remove the sanitized canonical working representation needed downstream
- cleanup must respect exception-retained records
- cleanup rules should be automatable and auditable

If SQLite file growth remains a problem after raw cleanup, compaction can be treated as an operations concern rather than a data model concern.

---

## 9. Observability For Sanitization Quality

The system should preserve lightweight metrics that help evaluate sanitizer effectiveness even after raw payload cleanup.

Recommended long-term metrics include:

- raw text length
- sanitized text length
- reduction ratio
- whether HTML was detected
- sanitization method used
- whether truncation occurred
- whether the item became low-context after cleaning

These metrics often have more long-term value than permanent storage of every raw payload.

---

## 10. Decisions Locked By This Rewrite

- raw input may be stored, but not forever by default
- sanitized working text is the canonical downstream text representation
- retention policy is mandatory, not optional
- cleanup is valid and expected, not a sign of data loss by itself
- exception retention must exist for special cases
