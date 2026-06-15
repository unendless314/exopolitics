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
- curation and publish transitions
- deletion and rebuild expectations

---

## 2. Lifecycle Principle

The key lifecycle rule is:

- raw input may enter first
- sanitized text becomes the downstream working form
- publish outputs are derived from approved canonical records

These are different lifecycle stages, not interchangeable copies of the same field.

---

## 3. Stage Flow

```text
raw feed item
  -> normalized ingest item
  -> sanitized working text
  -> classification result
  -> curation decision
  -> publish export
  -> site rendering
```

Recognized branch:

```text
curation decision
  -> edit workflow
  -> human review decision
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
- low-context items may resolve to `unknown`
- workflow retry policy belongs to classify, not ingest

---

## 7. Curation Lifecycle

Curation consumes classified items and determines whether they should:

- be approved
- be rejected
- be deleted under governance policy
- enter an edit-oriented workflow before publication

Curation is also where queue aging and SLA governance belong.

---

## 8. Publish Lifecycle

Approved canonical records move into a publish representation.

Publish output should:

- be derived from canonical records
- preserve provenance and disclosure data
- remain rebuildable if needed

Those canonical records may be either:

- approved source-derived records that are ready for direct aggregation-style publication
- approved edited records created through the edit workflow

The site must consume publish output, not canonical operational tables directly.

---

## 9. Rebuild And Deletion Rules

### 9.1 Rebuildable Layers

The following should be rebuildable:

- publish exports
- site output

### 9.2 Non-Rebuildable Decisions

The following are part of system history and should remain durable unless intentionally deleted under policy:

- ingest records
- classification outputs
- curation decisions

### 9.3 Retention-Governed Data

Raw input belongs to a retention-governed layer.

That means:

- it may be deleted on schedule
- deletion of raw input does not invalidate sanitized canonical records
- exception cases may bypass normal cleanup rules

---

## 10. Lifecycle Questions Locked By This Rewrite

- raw input and sanitized text are separate lifecycle stages
- sanitized text is created before classification
- raw cleanup is allowed and expected by default
- canonical downstream flow must not depend on indefinite raw retention

---

## 11. Temporal Policy and Historical Data

### 11.1 State-Driven Processing Pipeline
The system processes and stores all fetched items regardless of their publication date (`published_at`). There is no temporal filtering in the upstream ingestion, classification, or curation workflows.
- **Ingestion**: Ingest fetches all available feed data. De-duplication rules prevent duplicates, but any novel historical item (e.g. published years ago but fetched for the first time) is stored as a valid `source_item`.
- **Classification**: All newly ingested items are classified using the same content-based rules and LLM models.
- **Curation**: The curation queue processes all classified items based on state transitions, ensuring historical records are verified and enriched.

### 11.2 Downstream UI-Level Filtering
The responsibility of managing the user-facing temporal experience is deferred entirely to the downstream **site** (or publish-export) layer:
- **Breaking/Latest News Feed**: The front page or feed views should filter items by publication date (e.g., displaying only items with `published_at` in the last 7 days).
- **Search & Archives**: Users can query the complete, curated database containing all historical records.
- **Rationale**: This separates semantic relevance (content evaluation) from transient presentation guidelines (news age), ensuring the system builds a complete historical UAP database without cluttering the homepage UI.

