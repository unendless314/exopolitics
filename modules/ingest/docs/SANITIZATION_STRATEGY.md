# Sanitization Strategy

**Status:** Active rewrite draft  
**Updated:** 2026-06-08

---

## 1. Purpose

This document defines how `ingest` turns raw feed input into sanitized working text.

The purpose of sanitization is not to create publish-ready prose.
The purpose is to create a predictable downstream working representation for classification and curation support.

---

## 2. Observed Data Reality

Current local ingest data shows that feed content is often HTML-heavy and frequently oversized.

Observed direction from the existing dataset:

- most items contain HTML or HTML entities
- long and very long bodies are common
- some items become low-context after noise removal
- a small number of items are extreme outliers and need hard length controls

Because of this, sanitization must be a first-class ingest capability rather than an optional cleanup pass.

---

## 3. Default Strategy

The default implementation direction is:

- one shared sanitization pipeline for most sources
- small source-specific overrides for exceptional feeds
- no per-source custom parser framework in MVP unless repeated evidence justifies it

This keeps contracts stable while still allowing source-specific repair where necessary.

---

## 4. Input Priority

Sanitization should prefer raw text in this order unless a source override changes it:

1. entry summary or content field containing the richest source text
2. other entry text fields that are explicitly mapped by parser logic
3. normalized title as supplemental context, not as a substitute for missing body text

Important rules:

- title may be prepended or kept separately for downstream use, but this behavior must be consistent
- sanitization must not silently invent missing content
- raw input priority must be deterministic

---

## 5. Shared Pipeline

Minimum shared pipeline stages:

1. capture raw text candidate
2. detect whether HTML or encoded markup is present
3. remove `script`, `style`, and clearly non-content blocks
4. decode or flatten common entities when needed
5. flatten structural markup into readable text boundaries
6. normalize whitespace
7. apply content-length cap when needed
8. compute metrics and flags
9. persist sanitized working text and sanitization outcome

This is the baseline pipeline for most sources.

---

## 6. Source Overrides

Overrides are allowed for sources whose structure consistently defeats the shared pipeline.

Examples:

- content container selectors
- drop selectors for known boilerplate blocks
- length-cap override
- source-specific removal of repeated banner or footer text

Overrides must not become a hidden custom code framework.

The preferred order is:

1. improve the shared pipeline when the problem is common
2. add a small config override when the problem is source-specific
3. introduce custom code only after repeated evidence shows config is insufficient

---

## 7. Output Contract

Sanitization must produce:

- sanitized working text
- raw text length when measurable
- sanitized text length
- reduction ratio or equivalent metric when measurable
- HTML detected flag
- truncation flag
- text-processing status
- text-processing reason (nullable)
- sanitization method or version

The sanitized output must be clearly distinct from raw retained payloads in storage and downstream contracts.

---

## 8. Low-Context Rule

Sanitization may succeed technically while still producing text that is too thin for confident downstream use.

That outcome should be represented as low-context, not silently treated as a normal success.

MVP direction:

- keep low-context detection simple, deterministic, and easy to tune later
- prefer rule-based detection over subjective free-text judgment
- treat low-context as a downstream caution signal, not as an ingest failure by itself

`ingest` may persist:

- text-processing status
- text-processing reason when status is low_context or failed

`ingest` must not turn that into a classification decision.

### 8.1 Recommended MVP Checks

Recommended first-pass checks:

1. no usable body candidate remains after input selection
2. sanitized text becomes empty after cleanup
3. sanitized text length falls below a minimum threshold
4. sanitized text is almost entirely the title or title repetition
5. sanitized text is dominated by boilerplate or low-information fragments

Recommended implementation order:

1. run sanitization
2. compute simple metrics
3. apply fixed rules in priority order
4. set `text_processing_status`
5. store the first matching reason code

This keeps the initial implementation understandable and testable.

### 8.2 Recommended MVP Metrics

The first implementation does not need advanced NLP.

Simple metrics are enough:

- `sanitized_text_length`
- whether any body candidate existed before cleanup
- whether any text remained after cleanup
- rough title overlap signal
- simple boilerplate signals such as repeated "read more" or mostly-link text

Thresholds should be config or code constants that can be tuned later after observing real data.

### 8.3 Text Processing Outcome Codes

The V2 contract replaces `is_low_context` / `low_context_reason` with `text_processing_status` / `text_processing_reason`.

Allowed `text_processing_status` values:

- `completed`: text extraction and sanitization succeeded, content has sufficient context for classify
- `low_context`: text extraction and sanitization succeeded, but the result is too sparse for classify
- `failed`: the text-processing pipeline did not produce a valid result

Allowed `text_processing_reason` values under `low_context`:

- `post_cleanup_empty`
- `too_short`
- `title_only`
- `title_heavy`
- `template_heavy`
- `mostly_links`
- `truncated_to_low_context`

Allowed `text_processing_reason` values under `failed`:

- `missing_body`
- `sanitizer_exception`

Important rule:

- prefer compact stable reason codes over free-form prose in storage

### 8.4 Scope Boundary

- `low_context` means the cleaned text may be insufficient for stable downstream interpretation
- `low_context` does not mean the item should be dropped automatically; it remains stored as a valid ingested record
- `failed` means the text-processing pipeline encountered an engineering error; sanitization failures must not be labeled as `low_context`
- text-processing outcome does not mean `ingest` should make a classification judgment
- items with `text_processing_status` of `low_context` or `failed` are excluded from the classify pending queue, meaning they stop before classify-stage processing

---

## 9. Non-Goals

This strategy does not require:

- perfect article extraction for every source
- editorial rewriting
- summarization as a substitute for cleaning
- LLM-based cleanup in MVP
- a separate `sanitize` module at this stage

---

## 10. Decisions Locked By This Rewrite

- `ingest` uses a shared sanitization pipeline by default
- source-specific behavior should usually be expressed as config overrides
- text-processing outcomes (completed, low_context, failed) and truncation are first-class sanitization results
- sanitization creates a working representation, not publish-ready prose
