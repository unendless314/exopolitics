# Translate Module

This module is responsible for the asynchronous translation of finalized content into target languages (English, Japanese, etc.) before static export.

> [!IMPORTANT]
> **Co-location Disclaimer**: The `approved_content_record` table represents a shared canonical handoff capability (not owned solely by the `translate` module). For implementation simplicity, its schema migrations and assembly helper scripts are temporarily co-located under `modules/translate/`. This co-location does not alter the module boundaries defined in [MODULE_BOUNDARIES.md](file:///C:/Users/user/Documents/exopolitics/docs/MODULE_BOUNDARIES.md); `translate` remains a pure downstream consumer of this handoff table.

## Context and Purpose

To optimize operating costs and maintain high design flexibility, multi-language rendering is decoupled into two domains:
1. **Content Multilingualism** (Backend): Handled by this `translate` module and the SQLite database.
2. **UI Internationalization (i18n)** (Frontend): Handled by the static site generator (`site` module).

The translation pipeline occurs after editorial curation and editing:

```text
[ingest] ──> [classify] ──> [curate] ──> [approved_content_record] ──> [translate] ──> [publish] ──> [site]
                                     \-> [edit] ───────────────────/
```

- **Upstream Input**: Reads the unified `approved_content_record` representing the approved mother-draft as five structured plain-text fields (`display_title`, `summary_short`, `bullet_1`, `bullet_2`, `bullet_3`), materialized by the shared handoff assembler as a straight-through copy of upstream curation output with no UI presentation labels injected.
  *Note: `content_language_code` strictly designates the language of this finalized mother-draft (currently English), NOT the original source text language detected by `classify`.*
- **Downstream Output**: Writes translated five-field results to `translation_output`, which the `publish` module reads for static file export. Presentation labels (e.g. `Key Claim`) are applied later by the `site` module at build time, never in this pipeline.

## Key Features

- **Structured Five-Field Content**: The mother-draft is stored and translated as five plain-text fields (title, summary, three bullets) in a single API call per target language, isolating content from presentation concerns. Self-translation bypass copies the five fields directly without an API call.
- **Fingerprinting & Invalidation**: Tracks the canonical fingerprint of the upstream approved mother-draft to automatically invalidate translations if the draft is edited.
- **State Machine management**: Manages states (`pending`, `completed`, `failed`, `stale`) for each target language.

## Document Directory

- [DATA_CONTRACT.md](./DATA_CONTRACT.md): Database schemas, fingerprint formulas, and storage expectations.
- [PROMPT_CONTRACT.md](./PROMPT_CONTRACT.md): LLM inputs, structured output JSON schemas, prompts, and safety constraints.
- [EXECUTION_POLICY.md](./EXECUTION_POLICY.md): Queue selection, retry parameters, concurrency throttling, and transaction boundaries.
- [STATE_TRANSITIONS.md](./STATE_TRANSITIONS.md): Lifecycle states, trigger events, transition matrices, and invalidation rules.
- [TRANSLATION_POLICY.md](./TRANSLATION_POLICY.md): Style guides, plain-text field rules, and UAP terminology glossary.
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md): Development phases and epics for the translate module.

