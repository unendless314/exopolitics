# Translate Module

This module is responsible for the asynchronous translation of finalized content into target languages (English, Japanese, etc.) before static export.

## Context and Purpose

To optimize operating costs and maintain high design flexibility, multi-language rendering is decoupled into two domains:
1. **Content Multilingualism** (Backend): Handled by this `translate` module and the SQLite database.
2. **UI Internationalization (i18n)** (Frontend): Handled by the static site generator (`site` module).

The translation pipeline occurs after editorial curation and editing:

```text
[ingest] ──> [classify] ──> [curate] ──> [approved_content_record] ──> [translate] ──> [publish] ──> [site]
                                     \-> [edit] ───────────────────/
```

- **Upstream Input**: Reads the unified `approved_content_record` representing the approved mother-draft.
- **Downstream Output**: Writes translated results to `translation_output`, which the `publish` module reads for static file export.

## Key Features

- **Decoupled Architecture**: Spliced Markdown structure is translated as a whole, isolating formatting from upstream data schema variations.
- **Fingerprinting & Invalidation**: Tracks the canonical fingerprint of the upstream approved mother-draft to automatically invalidate translations if the draft is edited.
- **State Machine management**: Manages states (`pending`, `completed`, `failed`, `stale`) for each target language.

## Document Directory

- [DATA_CONTRACT.md](./DATA_CONTRACT.md): Database schemas, fingerprint formulas, state matrices, and DDL scripts.
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md): Implementation steps and epics for the translate module.
