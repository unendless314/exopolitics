# Implementation Roadmap

**Status:** Active rewrite draft  
**Updated:** 2026-06-05

---

## 1. Purpose

This document defines the recommended order of work after the planning reset.

The key principle is:

- lock contracts before rewriting implementation

Because the system is still pre-production and data can be rebuilt, the roadmap favors clean breaking changes over compatibility-heavy migration work.

---

## 2. Phase Order

### Phase 1: Lock Top-Level Contracts

Finish and review the new top-level docs:

- `PRD.md`
- `SYSTEM_OVERVIEW.md`
- `DATA_LIFECYCLE.md`
- `STORAGE_AND_RETENTION.md`
- `MODULE_BOUNDARIES.md`
- `CANONICAL_ENTITY_CONTRACT.md`

Goal:

- remove ambiguity before touching implementation

### Phase 2: Rewrite Ingest Data Contract

Update ingest module docs to reflect the new model:

- raw input representation
- sanitized working text representation
- retention-governed raw handling
- new source item contract

Goal:

- ensure `ingest` writes the right downstream representation

### Phase 3: Rewrite Canonical Schema Direction

Update schema planning and module-level storage docs so they express:

- long-term canonical fields
- short-retention raw fields or structures
- sanitization metrics
- explicit downstream text fields

Goal:

- stop overloading ambiguous text columns

### Phase 4: Rewrite Ingest Implementation

Change ingest code and schema together.

Expected work:

- adjust parsing and persistence path
- create sanitized working text during ingest
- preserve raw input only under the new retention-aware model
- rebuild the database from scratch if needed

Goal:

- make canonical ingest output match the rewritten contracts

### Phase 5: Rewrite Classify Contracts And Implementation

Update classify docs and code so the module:

- reads sanitized working text
- uses the new low-context rules
- no longer depends on ambiguous raw summary semantics

Goal:

- align classify with the rewritten ingest contract

### Phase 6: Curation, Translation, and Publish Planning Refresh

After ingest and classify contracts stabilize, refresh:

- curation queue contracts
- translation module contracts and LLM prompt design
- publish export contracts (multilingual static folders, slug rules, and coverage policies)
- edit workflow contracts needed for the immediate post-MVP phase

Goal:

- ensure downstream modules inherit the corrected upstream semantics
- align translation, slug generation, and static JSON output formats with the new multilingual content strategy
- ensure the post-MVP path to edit-assisted publishing is already aligned with the rewritten core pipeline

---

## 3. Recommended Validation Strategy

Before implementation is considered stable, validate with real source samples:

- inspect noisy feed summaries
- compare raw versus sanitized text side by side
- measure sanitized length and reduction ratio
- check low-context outcomes after cleaning
- verify classification prompt inputs are materially improved

This validation is mandatory because the rewrite exists specifically to correct a real data-quality problem.

---

## 4. Migration Strategy

Recommended strategy:

- prefer reset and rebuild over backward-compatibility work

Reason:

- the project is not in production
- stored data can be re-fetched
- preserving old schema semantics would keep the wrong contract alive

---

## 5. Deferred Work

Only after the new core pipeline stabilizes should the project decide whether to add:

- a separate raw staging store
- more advanced readability-based extraction
- shared external content retrieval capability
- a separate executable `edit` module

This deferral applies to extracting `edit` as a separately executable module, not to recognizing `edit` as a near-term product capability.

---

## 6. Immediate Next Step

After the top-level docs are accepted, the next concrete step should be:

- rewrite `modules/ingest/docs/` to match the new storage and lifecycle contracts
