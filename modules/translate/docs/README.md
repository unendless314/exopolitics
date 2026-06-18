# Translate Module

This module is responsible for the asynchronous translation of finalized content into target languages (English, Japanese, etc.) before static export.

> [!IMPORTANT]
> **Co-location Disclaimer**: The `approved_content_record` table represents a shared canonical handoff capability (not owned solely by the `translate` module). For implementation simplicity, its schema migrations and assembly helper scripts are temporarily co-located under `modules/translate/`. This co-location does not alter the module boundaries defined in [MODULE_BOUNDARIES.md](file:///C:/Users/user/Documents/derived-work/docs/MODULE_BOUNDARIES.md); `translate` remains a pure downstream consumer of this handoff table.

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

- [DATA_CONTRACT.md](./DATA_CONTRACT.md): Database schemas, fingerprint formulas, and storage expectations.
- [PROMPT_CONTRACT.md](./PROMPT_CONTRACT.md): LLM inputs, structured output JSON schemas, prompts, and safety constraints.
- [EXECUTION_POLICY.md](./EXECUTION_POLICY.md): Queue selection, retry parameters, concurrency throttling, and transaction boundaries.
- [STATE_TRANSITIONS.md](./STATE_TRANSITIONS.md): Lifecycle states, trigger events, transition matrices, and invalidation rules.
- [TRANSLATION_POLICY.md](./TRANSLATION_POLICY.md): Style guides, formatting preservation rules, and UAP terminology glossary.
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md): Development phases and epics for the translate module.

