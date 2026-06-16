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
- machine-generated descriptive signals for downstream curation triage

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

### 3.3 `curate`

Owns:

- curation queue behavior
- approval, rejection, deletion, and downstream action selection under editorial policy
- queue aging and SLA policy
- editorial curation over public exposure

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
- content review and revision

Must not own:

- source ingest identity
- public export rules
- site rendering
- translation execution

### 3.5 `translate`

Owns:

- translation of display titles, source attribution notes, and spliced content body markdown
- content translation versioning and source fingerprint matching
- language coverage status and quality states (pending, completed, failed, stale)
- translation LLM orchestration, prompt template loading, and rate limiting

May read:

- curation outputs (e.g., `curation_output`)
- finalized edited drafts (when human editor revisions are completed)

Must not own:

- editorial curation judgment (whether to publish or rewrite)
- static exporter layouts or static file folder writing (owned by `publish`)

### 3.6 `publish`

Owns:

- selection of completed translated records for export
- publish representation and slug generation
- attribution and disclosure emission

May read:

- completed translated records (`translation_output`)
- original source item metadata and canonical URL

Must not own:

- raw data collection
- classification logic
- translation LLM orchestration or cost decisions
- human editorial judgment itself

### 3.7 `site`

Owns:

- routes
- public presentation
- UI localization (i18n) and SEO concerns

May read:

- publish-layer outputs only

Must not own:

- canonical database writes
- curation state changes
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
- `curate` owns curation decision-making
- `translate` owns content translation and fingerprinted lifecycle
- `publish` owns export shape and slug generation
- `site` is a pure downstream consumer
