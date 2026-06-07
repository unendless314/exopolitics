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
- review and publish transitions
- deletion and rebuild expectations

---

## 2. Lifecycle Principle

The key lifecycle rule is:

- raw input may enter first
- sanitized text becomes the downstream working form
- publish outputs are derived from reviewed canonical records

These are different lifecycle stages, not interchangeable copies of the same field.

---

## 3. Stage Flow

```text
raw feed item
  -> normalized ingest item
  -> sanitized working text
  -> classification result
  -> review decision
  -> publish export
  -> site rendering
```

Recognized branch:

```text
review decision
  -> edit workflow
  -> review decision
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
- reviewer inspection support
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
- optional edit-candidate signal

### 6.3 Failure Semantics

- classification failure should not destroy the ingest record
- low-context items may resolve to `unknown`
- workflow retry policy belongs to classify, not ingest

---

## 7. Review Lifecycle

Review consumes classified items and determines whether they should:

- be approved
- be rejected
- be deleted under governance policy
- enter an edit-oriented workflow before publication

Review is also where queue aging and SLA governance belong.

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
- review decisions

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
