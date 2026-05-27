# UAP / UFO Aggregation Project Planning Workspace

This repository currently serves as the **planning workspace** for a modular UAP / UFO aggregation system.

At this stage, the root-level documentation is intentionally focused on **overview-level design** rather than module implementation details.

## Current Role Of This Repository

- Hold the top-level product and architecture documents
- Preserve the current RSS source research and category definitions
- Define module boundaries before implementation begins
- Provide the planning base for the future `ingest`, `classify`, `review`, `publish`, and `site` modules

## Documentation Layout

```text
docs/
├── PRD.md
├── TECH_SPEC.md
├── CONTENT_LIFECYCLE.md
├── MODULE_BOUNDARIES.md
└── comment.md
```

### Root `docs/`

These files are overview documents. They describe:

- product goals
- architecture direction
- storage model
- module boundaries
- content state flow

They do **not** attempt to fully specify any single module's internal implementation.

## Planned Repository Shape

```text
project-root/
├── docs/
└── modules/
    ├── ingest/
    ├── classify/
    ├── review/
    ├── publish/
    └── site/
```

Each module is expected to have its own:

- source code
- `config/`
- `docs/`
- tests

## Config Strategy

There is currently **no committed global `config/` strategy**.

The working assumption is:

- configuration belongs to the owning module first
- root-level `config/` should only be created later if truly shared settings emerge

For example, the existing RSS source definitions are better understood as future `ingest/config/` assets, not as system-wide configuration.

## Immediate Build Order

1. `ingest`
2. `classify`
3. `review`
4. `publish`
5. `site`

## Source Research

The current RSS source list was compiled from external source research and is intended to seed the future `ingest` module.
