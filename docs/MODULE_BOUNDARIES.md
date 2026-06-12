# Module Boundaries

**Status:** Active rewrite draft  
**Updated:** 2026-06-05

---

## 1. Purpose

This document defines ownership boundaries between modules so the rewritten system does not repeat ambiguous contracts.

It is intended to prevent:

- one module silently redefining another module's data meaning
- state changes being scattered across unrelated code
- storage concerns leaking into downstream logic without contract updates

---

## 2. Boundary Principles

- modules own decisions, not just code locations
- upstream modules must not force downstream modules to interpret ambiguous fields
- downstream modules may rely only on explicitly defined representations
- publish and site layers must remain downstream-only consumers

---

## 3. Module Ownership

### 3.1 `ingest`

Owns:

- source configuration
- feed fetching
- deduplication
- normalized source item persistence
- raw input capture policy
- sanitized working text generation
- source health and fetch execution records

May read:

- source config
- prior source state
- prior dedup markers

Must not own:

- LLM prompts
- topic classification decisions
- human approval decisions
- publish export formatting

Important boundary:

- `ingest` owns the transformation from raw feed input into sanitized working text
- `ingest` must not leave downstream modules guessing whether a field is raw or cleaned

### 3.2 `classify`

Owns:

- pending queue selection for unclassified items
- topic classification results
- classification rationale and confidence
- machine-generated descriptive signals for downstream review triage

May read:

- normalized source item metadata
- sanitized working text
- source URL and timestamp metadata

Must not own:

- raw feed retention policy
- feed fetching
- manual review decisions
- publish-layer output structure
- editorial action selection (e.g. translation, rewrite, or research decisions)

Important boundary:

- `classify` reads sanitized working text, not ambiguous raw summary fields

### 3.3 `review`

Owns:

- review queue behavior
- approval, rejection, deletion, and downstream action selection under editorial policy
- queue aging and SLA policy
- final human responsibility over public exposure

May read:

- source item metadata
- sanitized working text
- classification results
- selected raw evidence when retained and needed for investigation

Must not own:

- feed fetching
- model prompt design
- site rendering

### 3.4 `edit`

Owns:

- site-owned draft content
- source linking for edited content
- edit-specific metadata and responsibility fields

Must not own:

- source ingest identity
- public export rules
- site rendering

### 3.5 `publish`

Owns:

- selection of approved source-derived records and approved edited records for export
- publish representation
- attribution and disclosure emission

May read:

- approved canonical records
- source links and provenance data

Must not own:

- raw data collection
- classification logic
- human editorial judgment itself

### 3.6 `site`

Owns:

- routes
- public presentation
- i18n and SEO concerns

May read:

- publish-layer outputs only

Must not own:

- canonical database writes
- review state changes
- feed or classification configuration

---

## 4. Shared Capability Rules

Some capabilities may later be shared without becoming formal modules immediately.

Example candidate:

- external page retrieval or enrichment

Criteria for becoming shared:

- used by multiple modules
- stable enough to justify a common contract
- no longer just an implementation detail of one module

Until then, avoid inventing heavyweight shared systems too early.

---

## 5. Decisions Locked By This Rewrite

- `ingest` owns sanitization of feed input into downstream working text
- `classify` owns classification, not text cleanup
- `review` owns final public decision-making
- `publish` owns export shape
- `site` is a pure downstream consumer
