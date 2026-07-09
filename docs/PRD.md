# PRD

**Status:** Active rewrite draft  
**Updated:** 2026-06-05

---

## 1. Product Definition

This project is a UAP / UFO topic-focused aggregation system.

Its purpose is not to mirror full articles, run a discussion forum, or generate large volumes of AI-written content. The purpose is to operate a durable content pipeline that can:

- ingest feed items from many external sources
- preserve enough structured data for downstream processing and auditing
- classify items into topic-relevant buckets
- support curation and human review before public exposure
- publish approved outputs to a static public site

The product is fundamentally an **aggregation and curation system**.
An initial pipeline MVP may publish approved source-derived outputs first, but product usability beyond that initial stage is expected to rely on edit-assisted publishing.

---

## 2. Product Goals

The system should:

- maintain a stable, repeatable ingest pipeline for topic-relevant sources
- reduce human review load without giving final editorial control to automation
- preserve source attribution and editorial accountability
- keep the public site focused on approved and interpretable content only
- support near-term growth into edit-assisted publishing without collapsing source content and site-owned content into one model
- support operator-facing reporting for source health, workflow efficiency, and cost visibility without affecting the core publishing pipeline

---

## 3. Intended Users

Primary readers:

- readers who want a focused stream of UAP / UFO coverage
- researchers or hobbyists tracking disclosure, sightings, investigations, and related reporting

Primary operators:

- the site owner or editor running the pipeline
- future human curators/reviewers responsible for approval and rejection decisions

---

## 4. Core Content Principles

### 4.1 Save Before Display

- ingesting an item and publicly displaying an item are separate decisions
- items may be stored even if they are never published
- deletion is a governance action, not an automatic side effect of classification

### 4.2 Topic Classification Is A Filtering Aid

Initial topic classes are:

- `core`
- `adjacent`
- `irrelevant`
- `unknown`

These classes exist to reduce curation burden and organize attention.
They do not replace human judgment.

### 4.3 Human Review Owns Final Public Decisions

- automation may assist prioritization and triage
- automation must not become the unreviewed public publishing authority
- public-facing outputs must remain attributable to a human owner or operator

### 4.4 Source Attribution And Transparency Are Required

- public outputs must preserve source attribution
- site-owned edited content must remain distinguishable from aggregated source items
- AI participation must remain disclosable where relevant

---

## 5. Practical Data Principles

The system must distinguish between:

- raw feed input
- sanitized downstream text
- curation, edit, review, translation, and publish decisions

This distinction exists because raw feed metadata is often noisy, HTML-heavy, and unsuitable for direct downstream classification.

The product requirement is therefore:

- raw input may be retained for validation and debugging
- sanitized text is the downstream working representation
- retention of raw input is a policy choice, not a reason to contaminate downstream contracts

---

## 6. MVP Scope

MVP includes:

- source configuration and scheduled feed ingestion
- deduplication and canonical storage of normalized source items
- sanitized text generation for downstream classification
- initial classification pipeline
- curation, edit, and review workflows
- translate and publish export for approved content
- static site consumption of publish outputs

MVP does not require:

- full-page scraping as a mandatory step for every item
- permanent retention of all raw feed payloads
- user accounts
- comments or community features
- fully autonomous AI publishing
- immediate extraction of a separate standalone `edit` runtime module

This does not mean `edit` is optional as a product capability. It means only that the first MVP does not need `edit` to ship as a separately executable module.

---

## 7. Success Criteria

The MVP is successful when:

- ingest runs reliably enough to keep source coverage current
- classification reduces human curation, edit, and review burden without destroying topic precision
- reviewers and operators can inspect and decide on pending content with clear provenance
- the public site only depends on approved publish outputs
- raw-versus-sanitized handling is explicit and no longer ambiguous in contracts
- storage growth remains governable through retention policy rather than accidental bloat

---

## 8. Non-Goals

The system is not intended to be:

- a full-text archive of third-party news sites
- a generic CMS for arbitrary publishing workflows
- a forum or social platform
- a one-click AI rewriting factory
- a design that assumes permanent retention of every raw payload forever

---

## 9. Product Decisions Locked By This Rewrite

- the system remains modular
- canonical storage remains the main system record
- raw input and sanitized working text must be modeled separately
- classification reads sanitized working text rather than ambiguous raw summary fields
- raw retention is allowed but must be policy-driven and time-bounded by default
- edited site-owned content remains a separate content type from source-ingested items

---

## 10. Deferred Product Questions

- when, if ever, full-page retrieval becomes a common shared capability
- when `edit` should become an independently executable module instead of remaining a curation-adjacent workflow for a short period
- what retention window is best for raw input in early production
- whether certain sources deserve custom sanitization rules
