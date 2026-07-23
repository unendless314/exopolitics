# Data Lifecycle

**Status:** Active rewrite draft  
**Updated:** 2026-06-05

---

## 1. Purpose

This document defines how data moves through the rewritten system and how each representation changes over time.

It focuses on:

- raw input
- sanitized working text
- classification outputs
- curation, edit, translation, and publish transitions
- deletion and rebuild expectations

---

## 2. Lifecycle Principle

The key lifecycle rule is:

- raw input may enter first
- sanitized text becomes the downstream working form
- publish outputs are derived from approved and translated canonical records

These are different lifecycle stages, not interchangeable copies of the same field.

---

## 3. Stage Flow

```text
raw feed item
  -> normalized ingest item
  -> sanitized working text
  -> [text_processing_status boundary]
       |-- failed                              -> stop before classify (processing failure)
       |-- low_context: post_cleanup_empty     -> stop before classify (no usable cleaned text)
       |-- completed / low_context (all other reasons) -> classification result
            -> curation decision (approved)
            -> approved content record (finalized mother-draft)
            -> translation output (completed translated records)
            -> publish export
            -> site rendering
```

Read-only Side-output:
```text
canonical storage -> analysis -> reports/analysis/
```

When an item is manually withdrawn:
```text
curation decision (withdrawn)
  -> approved content record & translation output (preserved in DB as cache anchors)
  -> publish export (physically removed from site distribution)
```

Recognized branch:

```text
curation decision
  -> edit workflow (human editorial draft / revision)
  -> approved content record (finalized mother-draft)
  -> translation output (completed translated records)
```

---

## 4. Raw Input Lifecycle

### 4.1 Entry

Raw feed input enters during ingest.

Examples:

- feed title
- feed summary or description
- embedded HTML fragments
- feed-level timestamps and URLs

### 4.2 Use

Raw input is useful for:

- sanitizer verification
- debugging source quality issues
- investigating unexpected classification behavior

### 4.3 Retention

Raw input is not automatically a forever artifact.

Default direction:

- retain for a bounded window
- allow exception retention for specially marked cases

---

## 5. Sanitized Working Text Lifecycle

### 5.1 Creation

Sanitized working text is produced during ingest from raw feed input.

Minimum transformation expectations:

- strip obvious HTML noise
- remove script/style content when present
- normalize whitespace
- apply a controlled length cap when needed

### 5.2 Role

Sanitized working text is the default downstream representation for:

- classification
- curation inspection support
- future enrichment decisions

### 5.3 Durability

Sanitized working text belongs in canonical long-term storage because it is the durable operational representation the rest of the pipeline depends on.

---

## 6. Classification Lifecycle

### 6.1 Input

Classification consumes:

- `title`
- sanitized working text
- selected item metadata such as URL or timestamp

### 6.2 Output

Classification produces:

- topic class
- reason
- confidence
- optional attributes and signals

### 6.3 Failure Semantics

- classification failure should not destroy the ingest record
- items with `text_processing_status` of `failed`, or `text_processing_reason` of `post_cleanup_empty`, do not generate canonical classification rows and terminate before classify
- low-context items with any other reason continue to classification
- workflow retry policy belongs to classify, not ingest

---

## 7. Curation Lifecycle

Curation consumes classified items and determines whether they should:

- be approved
- be rejected
- be deleted under governance policy
- enter an edit-oriented workflow before translation
- be manually withdrawn or re-approved by an operator (transitioning its status to withdrawn without deleting downstream translation caches)

Curation is also where queue aging and SLA governance belong.

---

## 8. Translation Lifecycle

Translation consumes finalized mother-drafts (`approved_content_record`), checks for fingerprint changes, and calls LLMs to produce translated versions.

### 8.1 Input
Translation reads:
- `approved_content_record` (representing either direct curation approvals or finalized edited drafts)

### 8.2 Output
Translation produces:
- `translation_output` containing display titles, spliced markdown body content, and metadata (source fingerprint, translation status, model name, and prompt version) for each configured language code (e.g., `'zh'`, `'en'`, `'ja'`).

### 8.3 Lifecycle and Invalidation
- A translation record tracks the parent content via `parent_content_id` and is bound to the content state using `translation_output.source_fingerprint`.
- Whenever the `translate` runner runs, it compares `translation_output.source_fingerprint` with `approved_content_record.content_fingerprint`.
- If the fingerprint mismatches (indicating the upstream title or content has changed), the translation status transitions to `stale`, triggering a re-translation.
- If the translation process encounters an error, the status transitions to `failed` to trigger retries.

---

## 9. Publish Lifecycle

The `publish` module consumes completed translation records (`translation_output` in `completed` status) and exports them into static public assets.

Publish output should:

- be derived from `translation_output` records where `translation_status = 'completed'` and the upstream curation decision remains actively approved
- follow the configured Language Coverage Policy (e.g., Strict Match)
- generate uniform SEO-friendly URL slugs using English translated titles
- preserve provenance and disclosure data
- remain rebuildable if needed
- synchronize exported assets by removing public outputs when items are withdrawn upstream

The site must consume publish output, not canonical operational tables directly.

---

## 10. Side-Output Lifecycle

The `analysis` module operates out-of-band as a downstream sidecar.

### 10.1 Properties
- analysis outputs are observational derivatives of canonical state
- failure to generate analysis outputs does not invalidate or roll back canonical pipeline state

### 10.2 Flow
- `analysis` reads canonical storage and static config assets
- aggregates metrics and computes reporting outputs
- writes derived report files to `reports/analysis/`

---

## 11. Rebuild And Deletion Rules

### 11.1 Rebuildable Layers

The following should be rebuildable:

- publish exports
- site output

### 11.2 Non-Rebuildable Decisions

The following are part of system history and should remain durable unless intentionally deleted under policy:

- ingest records
- classification outputs
- curation decisions
- translation outputs

### 11.3 Retention-Governed Data

Raw input belongs to a retention-governed layer.

That means:

- it may be deleted on schedule
- deletion of raw input does not invalidate sanitized canonical records
- exception cases may bypass normal cleanup rules

---

## 12. Lifecycle Questions Locked By This Rewrite

- raw input and sanitized text are separate lifecycle stages
- sanitized text is created before classification
- raw cleanup is allowed and expected by default
- canonical downstream flow must not depend on indefinite raw retention
- approved content handoff is assembled from finalized curation approvals or finalized edited drafts before downstream processing
- translation pulls data from `approved_content_record` rather than accepting direct upstream writes into translation-owned storage
- publish exports only consume completed translation records of actively approved items, and synchronize removals when items are withdrawn
- only items with `text_processing_status` of `failed` or `text_processing_reason` of `post_cleanup_empty` terminate before classify and do not generate downstream classification records; low-context items otherwise continue to classification

---

## 13. Temporal Policy and Historical Data

### 13.1 State-Driven Processing Pipeline
The system processes and stores all fetched items regardless of their publication date (`published_at`). There is no temporal filtering in the upstream ingestion, classification, or curation workflows.
- **Ingestion**: Ingest fetches all available feed data. De-duplication rules prevent duplicates, but any novel historical item (e.g. published years ago but fetched for the first time) is stored as a valid `source_item`.
- **Classification**: All newly ingested items are classified using the same content-based rules and LLM models.
- **Curation**: The curation queue processes all classified items based on state transitions, ensuring historical records are verified and enriched.

### 13.2 Downstream UI-Level Filtering
The responsibility of managing the user-facing temporal experience is deferred entirely to the downstream **site** (or publish-export) layer:
- **Breaking/Latest News Feed**: The front page or feed views should filter items by publication date (e.g., displaying only items with `published_at` in the last 7 days).
- **Search & Archives**: Users can query the complete, curated database containing all historical records.
- **Rationale**: This separates semantic relevance (content evaluation) from transient presentation guidelines (news age), ensuring the system builds a complete historical UAP database without cluttering the homepage UI.
