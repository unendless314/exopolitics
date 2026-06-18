# Multilingual Content Strategy

**Status:** Active rewrite draft  
**Updated:** 2026-06-18  

---

## 1. Purpose

This document defines the cross-module contracts and architectural policies for multilingual content in the rewritten system. 

It establishes:
- The distinction between content-level translation and frontend UI localization.
- How translations are invalidated when source drafts are edited.
- The lifecycle rules for URL slugs across languages.

Detailed SQLite schema structures and CLI specifications are owned locally by the respective modules under `modules/translate/docs/` and `modules/publish/docs/`.

---

## 2. Core Architectural Principles

### 2.1 Content Multilingualism vs. UI i18n
1. **Content Multilingualism**: Refers to translated article metadata (titles, Markdown bodies). This is a backend content concern, stored in canonical storage, and generated before public static export.
2. **UI Internationalization (i18n)**: Refers to static interface labels (e.g., "Read More", "Home") and routing mechanisms. This is owned downstream by the `site` module and handled in frontend presentation.

### 2.2 Pipeline Sequence
Translation is performed downstream of curation and editing:

```text
[ingest] ──> [classify] ──> [curate] ──> [approved_content_record] ──> [translate] ──> [publish] ──> [site]
                                     \-> [edit] ───────────────────/
```

- `translate` reads approved mother-drafts from `approved_content_record`.
- `publish` reads completed translations from `translation_output` and writes static JSON files.

---

## 3. Fingerprinting & Invalidation Policy

To ensure translation accuracy and prevent stale content:
- **Single Source of Truth**: The canonical state version of a mother-draft is represented by `approved_content_record.content_fingerprint`.
- **Cache Validation**: The `translate` module must store this fingerprint in its database outputs and compare it against the upstream fingerprint during runs.
- **Invalidation**: Any change to `approved_content_record.content_fingerprint` or the runner configuration (model, prompt version) marks the corresponding translation as `stale`, triggering re-translation.

---

## 4. Language Coverage Policy

The system supports publishing content in multiple target languages. The exact language set is an operational configuration concern rather than a top-level architectural constraint.

### 4.1 Coverage Policies
1. **Strict Match (MVP Default)**: An article is only exported to the public site if all configured target languages are successfully translated and in `completed` status.
2. **Alternative downstream policies may exist**: Future publish-layer policies may allow partial language availability, but those behaviors are owned and defined by the `publish` and `site` layers rather than by this top-level contract.

The active coverage policy is owned by the `publish` layer and should remain configurable without changing the canonical translation entity structure.

---

## 5. Slug Permanency Policy

To optimize SEO and prevent broken links:
- **First-Publish Freeze**: The URL slug is generated during the first successful publication of an article, typically derived from its English translation title.
- **Immutable Slug Registry**: Once generated and stored in the canonical publish reference, the slug is permanently frozen. Subsequent updates to the title or content must not recalculate or overwrite this slug.
