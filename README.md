# UAP / UFO Aggregation Planning Workspace

This repository is currently the planning workspace for a modular UAP / UFO aggregation system.

The top-level documents focus on system architecture, data lifecycle, storage policy, and module boundaries. They do not try to lock implementation details too early.

This repository is currently in a documentation reset phase:

- `docs/` contains the active rewritten planning set
- `docs_archive/2026-06-reset/` preserves superseded planning as historical reference
- `modules_archive/` preserves superseded module docs and code as historical reference

## Current Scope

- define the top-level product and architecture direction
- preserve RSS source research and category definitions
- establish module boundaries before implementation expands
- document the development order for `ingest`, `classify`, `curate`, `edit`, `translate`, `publish`, `site`, and `analysis`
- reserve a compliant path for future edit content with source attribution and AI disclosure

## Architecture Summary

The current system direction is:

- `ingest`
  - fetch RSS/feed sources, deduplicate, persist canonical records, and generate sanitized working text
- `classify`
  - run initial topic classification against sanitized working text, including `unknown`, and candidate tagging
- `curate`
  - perform editorial curation, triage, formatting, and routing decisions
- `edit`
  - reserved for near-term site-native drafts, summaries, rewrites, and synthesis
  - in early stages, low-volume edit flow can remain inside `curate` before separate extraction
- `translate`
  - read approved `approved_content_record` mother drafts, generate multilingual translations, and write `translation_output`
- `publish`
  - export approved content into a publish layer
- `site`
  - build the public static site from publish-layer data
- `analysis`
  - read-only diagnostics, operational reporting, and metric aggregation against canonical storage, writing reports to `reports/analysis/`

## Development Priority

1. `ingest`
2. `classify`
3. `curate`
4. `edit` (only when the workflow becomes stable)
5. `translate`
6. `publish`
7. `site`
8. `analysis` (currently the active focus for read-only reporting and performance metrics)

This order is intentional. The project first stabilized ingestion, classification, curation, and translation around the canonical content handoff before moving on to a dedicated publish/export layer. The standalone `edit` module remains reserved for when that workflow becomes stable enough to justify extraction. Now that core pipeline contracts have stabilized, the active focus is integrating the `analysis` module as a read-only sidecar.

## Content Model Direction

At the current architecture level, public content is treated as two high-level origin types:

- `aggregated`
- `edit`

The current recommendation is to keep this model simple in MVP.

If edit workflows become stable, a separate derivation or method dimension can be added later for cases such as:

- `summary`
- `rewrite`
- `synthesis`
- `commentary`

## Repository Layout

```text
project-root/
├── docs/
├── docs_archive/
├── modules_archive/
└── modules/
    ├── ingest/
    └── ...
```

Notes:

- `docs/` contains top-level system documents
- `modules_archive/` contains pre-reset module snapshots and is reference-only
- module-level `config/` should remain the default ownership model
- root-level shared config should only appear later if a setting is truly cross-module

## Documentation Layout

```text
docs/
├── PRD.md
├── SYSTEM_OVERVIEW.md
├── DATA_LIFECYCLE.md
├── STORAGE_AND_RETENTION.md
├── MODULE_BOUNDARIES.md
├── IMPLEMENTATION_ROADMAP.md
├── RESET_DECISION.md
└── README.md
```

These documents cover:

- product goals
- system architecture direction
- data lifecycle
- storage and retention policy
- module ownership and implementation order

Archived top-level planning lives under `docs_archive/2026-06-reset/`.

## Current Module Workspace

The active module workspace has been reset and rebuilt. The tree currently includes:

```text
modules/
├── ingest/
│   ├── config/
│   ├── docs/
│   ├── src/
│   └── tests/
├── classify/
│   ├── config/
│   ├── docs/
│   ├── src/
│   └── tests/
├── curate/
│   ├── config/
│   ├── docs/
│   ├── src/
│   └── tests/
├── translate/
│   ├── config/
│   ├── docs/
│   ├── src/
│   └── tests/
├── publish/
│   ├── config/
│   ├── docs/
│   ├── src/
│   └── tests/
├── site/
│   ├── docs/
│   ├── src/
│   └── tests/
└── analysis/
    ├── config/
    └── docs/
```

At this stage, `modules/ingest/`, `modules/classify/`, `modules/curate/`, `modules/translate/`, `modules/publish/`, and `modules/site/` are fully implemented, executable, and validated. The `analysis` module's architecture and planning documents are locked, and it is the current active focus for implementation.

Pre-reset module docs and code now live under `modules_archive/`.

## Source Research

RSS source research and prior module artifacts from before the reset now live under `modules_archive/` until the new ingest config contract is rewritten.
