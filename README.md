# UAP / UFO Aggregation Planning Workspace

This repository is currently the planning workspace for a modular UAP / UFO aggregation system.

The top-level documents focus on system architecture, content flow, and module boundaries. They do not try to lock implementation details too early.

## Current Scope

- define the top-level product and architecture direction
- preserve RSS source research and category definitions
- establish module boundaries before implementation expands
- document the development order for `ingest`, `classify`, `review`, optional `edit`, `publish`, and `site`
- reserve a compliant path for future edit content with source attribution and AI disclosure

## Architecture Summary

The current system direction is:

- `ingest`
  - fetch RSS/feed sources, deduplicate, persist canonical records
- `classify`
  - run initial topic classification and candidate tagging
- `review`
  - perform human review and state transitions
- `edit`
  - reserved for future site-native drafts, summaries, rewrites, and synthesis
  - in early stages, low-volume edit flow can remain inside `review`
- `publish`
  - export approved content into a publish layer
- `site`
  - build the public static site from publish-layer data

## Development Priority

1. `ingest`
2. `classify`
3. `review`
4. `edit` (only when the workflow becomes stable)
5. `publish`
6. `site`

This order is intentional. The project should first stabilize data ingestion and review before introducing a dedicated edit workflow.

## Content Model Direction

At the current architecture level, public content is treated as two high-level origin types:

- `aggregated`
- `edit`

The current recommendation is to keep this model simple in MVP.

If future edit workflows become stable, a separate derivation or method dimension can be added later for cases such as:

- `summary`
- `rewrite`
- `synthesis`
- `commentary`

## Repository Layout

```text
project-root/
├── docs/
├── config/
└── modules/
    ├── ingest/
    ├── classify/
    ├── review/
    ├── edit/
    ├── publish/
    └── site/
```

Notes:

- `docs/` contains top-level system documents
- `config/` currently holds transitional planning assets, mainly for future `ingest`
- module-level `config/` should remain the default ownership model
- root-level shared config should only appear later if a setting is truly cross-module

## Documentation Layout

```text
docs/
├── PRD.md
├── TECH_SPEC.md
├── CONTENT_LIFECYCLE.md
├── MODULE_BOUNDARIES.md
└── comment.md
```

These documents cover:

- product goals
- architecture direction
- content lifecycle
- storage and publication boundaries
- module ownership and implementation order

## Current Module Workspace

The first module workspace has already been established:

```text
modules/
└── ingest/
    ├── config/
    ├── docs/
    ├── src/
    └── tests/
```

At this stage, `modules/ingest/` is still documentation-first. The goal is to refine system direction before committing to detailed implementation.

## Source Research

The current RSS source list and category definitions are intended to seed the future `ingest` module.
