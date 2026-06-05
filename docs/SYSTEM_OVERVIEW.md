# System Overview

**Status:** Active rewrite draft  
**Updated:** 2026-06-05

---

## 1. Purpose

This document describes the top-level system shape for the rewritten planning set.

It focuses on:

- processing stages
- storage layers
- module ownership at a high level
- what the public site is allowed to depend on

It does not define module-internal implementation details.

---

## 2. System Shape

```text
External Feed Sources
  -> ingest
  -> canonical storage
  -> classify
  -> review
  -> publish
  -> site
```

Optional future branch:

```text
review
  -> edit workflow
  -> review
```

---

## 3. Core Architectural Principles

- canonical storage is the main system record
- publish output is derived and rebuildable
- the public site must not write to canonical storage
- raw input and downstream working text are different representations
- module boundaries should follow ownership of decisions, not convenience of code placement

---

## 4. Canonical Storage Role

Canonical storage exists to keep the durable system record for:

- normalized source items
- sanitized working text
- source health and ingest execution metadata
- classification outputs
- review decisions
- publishable content references

Canonical storage is not required to retain every raw payload forever.
Retention policy is a separate concern from canonical identity.

---

## 5. Representation Layers

The rewritten system recognizes at least three important content representations:

### 5.1 Raw Feed Representation

- what the feed originally provided
- useful for debugging, validation, and sanitizer evaluation
- not safe as the default downstream LLM input

### 5.2 Sanitized Working Representation

- cleaned text derived from raw feed content
- intended for classification and downstream review support
- must be contractually defined and predictable

### 5.3 Publish Representation

- approved outputs prepared for public consumption
- derived from canonical records
- optimized for site rendering rather than operational workflow

---

## 6. Module Roles

### 6.1 `ingest`

Owns:

- source config loading and validation
- feed fetching
- deduplication
- normalized item persistence
- sanitized working text generation
- source health tracking

### 6.2 `classify`

Owns:

- pending item selection
- initial topic classification
- confidence and rationale persistence
- edit-candidate signaling when needed

### 6.3 `review`

Owns:

- approval and rejection decisions
- queue governance
- final human responsibility over public exposure

### 6.4 `publish`

Owns:

- selecting approved records for export
- generating publish-layer outputs
- preserving attribution and disclosure in exported form

### 6.5 `site`

Owns:

- public presentation
- routing and page generation
- static rendering concerns

---

## 7. Why The Rewrite Changes The Architecture Direction

The prior planning set treated feed summary fields too loosely.

The rewritten direction makes one explicit correction:

- downstream modules must not rely on an ambiguous text field whose meaning shifts between raw feed summary and cleaned working text

This is not a cosmetic change. It is the architectural fix that allows classification cost, quality, and retention policy to be governed separately.

---

## 8. Out Of Scope For This Layer

This document does not lock:

- exact schema column names
- exact retention durations
- exact sanitization algorithm details
- CLI command layout
- review UI design
