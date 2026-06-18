# Canonical Entity Contract

**Status:** Active rewrite draft  
**Updated:** 2026-06-08

---

## 1. Purpose

This document defines the minimum canonical entity set shared across modules in the rewritten system.

It exists to lock:

- which long-lived entities exist in canonical storage
- which module owns each entity family
- which representations are safe for downstream modules to read

It does not lock:

- exact table names
- exact column names
- database-specific DDL
- module-internal execution details

---

## 2. Canonical Entity Principles

- canonical storage keeps the durable operational record
- raw input and sanitized working text are different entities with different retention expectations
- module ownership follows decision ownership, not convenient table placement
- downstream modules may read only explicitly defined canonical representations
- publish and site layers are downstream derivatives, not canonical writers

---

## 3. Minimum Canonical Entity Set

The rewritten system must recognize at least these canonical entity families:

1. source item identity and metadata
2. sanitized working text
3. raw retained evidence
4. source state and fetch execution history
5. ingest deduplication state
6. classification result
7. curation decision
8. edit-owned draft or edited content when that workflow is active
9. approved content record (representing the finalized publication mother-draft)
10. translation output
11. publishable record or publish reference

The list above is a logical contract, not a requirement that every family live in a single table.

---

## 4. Entity Families

### 4.1 Source Item Identity And Metadata

This entity family represents the durable source-derived record created by `ingest`.

Minimum semantic contents:

- source identity
- feed item identity when available
- canonical source URL when available
- normalized title
- published timestamp when available
- ingest timestamps and dedup identity

Ownership:

- written by `ingest`
- readable by `classify`, `curate`, and `publish`

Important rule:

- this entity family must not be the storage location for ambiguous mixed raw-versus-sanitized text semantics

### 4.2 Sanitized Working Text

This entity family represents the cleaned downstream working text derived during `ingest`.

Minimum semantic contents:

- stable link to the source item record
- sanitized text body
- sanitization method or version
- sanitization quality or low-context signals
- durable metrics needed after raw cleanup

Ownership:

- written by `ingest`
- readable by `classify` and `curate`

Important rule:

- this is the default downstream text representation for operational workflow

### 4.3 Raw Retained Evidence

This entity family represents retention-governed raw payloads or fragments captured during `ingest`.

Minimum semantic contents:

- stable link to the source item record
- raw payload or fragment
- capture kind
- retained timestamp
- retention classification or exception marker

Ownership:

- written by `ingest`
- readable only when debugging, validation, or investigation requires it

Important rule:

- raw retained evidence is not the default downstream text contract

### 4.4 Source State And Fetch Execution History

This entity family represents mutable source health plus immutable run and attempt history.

Minimum semantic contents:

- current source fetch health
- cache validators when available
- run-level execution records
- source-attempt outcomes and counts

Ownership:

- written by `ingest`
- primarily operationally consumed by `ingest`

### 4.5 Ingest Deduplication State

This entity family represents explicit dedup identity control for source-derived items.

Minimum semantic contents:

- dedup key
- dedup rule
- stable link to the canonical source item

Ownership:

- written by `ingest`
- operationally consumed by `ingest`

### 4.6 Classification Result

This entity family represents the initial machine classification outcome.

Minimum semantic contents:

- stable link to the classified source item
- topic class
- confidence
- rationale or reason
- structured descriptive signals (such as content density, text quality, language, and official involvement)
- optional experimental metadata sandbox signals
- classifier metadata needed for auditability

Ownership:

- written by `classify`
- readable by `curate`

### 4.7 Curation Decision

This entity family represents automated editorial curation, triage, formatting, and routing decisions.

Minimum semantic contents:

- stable link to the curated canonical record
- decision outcome
- action semantics when applicable
- responsible actor metadata
- decision timestamp
- optional notes or governance context

Ownership:

- written by `curate`
- readable by `translate` and `edit`

### 4.8 Edit-Owned Draft Or Edited Content

This entity family exists only when edited content becomes part of the active workflow.

Minimum semantic contents:

- edited or drafted text
- linkage to source-derived records when applicable
- edit responsibility and provenance metadata

Ownership:

- written by `edit`
- readable by human review, `publish`, and `translate`

### 4.9 Approved Content Record

This entity family represents the finalized publication mother-draft ready for translation and public static export. It is the single canonical handoff artifact representing the publishable state, assembled from finalized upstream editorial states.

Minimum semantic contents:

- stable link to the source item record (`source_item_id`)
- display title (finalized title, either directly approved from curation or edited by human operators)
- content body (finalized Markdown body, spliced from curation outputs or edited by human operators)
- content fingerprint (`content_fingerprint`) representing the SHA-256 hash of the title and body
- approved timestamp
- author/editor metadata (identifying the responsible user or system configuration version)

Ownership:

- produced from finalized `curate` approvals or finalized `edit` outputs through the shared handoff capability
- readable by `translate`

### 4.10 Translation Output

This entity family represents the translated content ready for publication.

Minimum semantic contents:

- stable link to the approved content record (`parent_content_id`)
- stable link to the source item record (`source_item_id`) for grouping
- language identifier (`language_code`)
- target language display title (`display_title`)
- target language content (spliced Markdown body text)
- source fingerprint (`source_fingerprint`) used for change detection and cache validation
- quality/progress state (`translation_status`)
- LLM runtime configuration (`model_name`, `prompt_version`)
- timing fields (`translated_at`, `created_at`)

Ownership:

- written by `translate`
- readable by `publish`

### 4.11 Publishable Record Or Publish Reference

This entity family represents the approved output selected for export.

Minimum semantic contents:

- approved canonical source reference (`source_item_id`)
- approved content record reference (`parent_content_id`)
- permanently frozen URL slug (`slug`), generated upon first publication
- export-ready provenance and disclosure fields
- publish-layer record identity or reference

Ownership:

- written by `publish`
- readable by `site`

---

## 5. Representation Boundaries

The top-level canonical model recognizes four non-interchangeable content representations:

1. raw retained evidence
2. sanitized working text
3. translation representation (spliced multi-lingual database structure)
4. publish representation (static multilingual JSON directories, indexes, and feeds)

Boundary rules:

- `classify` reads sanitized working text, not raw retained evidence by default
- `curate` may inspect sanitized working text by default and raw retained evidence only when needed
- `translate` reads approved content records (`approved_content_record`), and writes translation outputs
- `publish` reads completed translation outputs only
- `site` reads publish-layer outputs only
- cleanup of raw retained evidence must not invalidate the source item record or sanitized working text record

---

## 6. Ownership Summary

- `ingest` owns source item identity, sanitized working text, raw retained evidence, source state, fetch history, and dedup state
- `classify` owns classification result
- `curate` owns curation decision
- `edit` owns edited content records
- the shared handoff capability assembles approved content records from finalized upstream editorial state
- `translate` owns translation output, quality states, and source content fingerprinting
- `publish` owns publish-layer records or references (and manages frozen slug registry)
- `site` does not own canonical database writes

---

## 7. Decisions Locked By This Contract

- the canonical model separates source identity, sanitized working text, raw retained evidence, and translation outputs
- `ingest` is responsible for creating the sanitized working representation before classification
- downstream modules must not reinterpret ambiguous feed summary fields as canonical working text
- translation outputs must separate language-specific representations from curation and edit schemas
- `approved_content_record` is the single canonical entity representing the publishable mother-draft; it is assembled from finalized curation or edit outcomes, and downstream modules read from it by pull
- translation outputs must point to the unified `approved_content_record` instead of the raw `source_item_id` to prevent update drift
- `approved_content_record.content_fingerprint` is the canonical fingerprint representing the mother-draft state; `translate` stores and compares against this to determine staleness
- URL slug generation occurs on first successful publication and is permanently frozen in canonical storage to prevent broken links
- top-level docs lock entity families and ownership, while module docs lock implementation-facing schema details
