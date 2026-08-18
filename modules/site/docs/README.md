# Site Module

**Document version:** v1.1
**Updated:** 2026-08-18
**Status:** Active draft

---

## 1. Module Positioning

`site` is the final, downstream presentation module in the active aggregation pipeline:

`ingest -> classify -> curate -> edit (when needed) -> translate -> publish -> site`

The module reads static, pre-compiled JSON files written by the `publish` module — the live versioned generation under `data/publish_export/generations/`, selected through the atomic `current.json` pointer — and compiles them into a static web application using Astro.

In the current architecture:
- `site` does not read from canonical operational database tables directly.
- `site` does not write to the canonical database.
- `site` owns only UI layouts, styles, dynamic routing patterns, UI localization (i18n), and SEO elements.
- **Active Development Target**: The active target is **Phase 2 (Hybrid Ingestion Stabilization)**. The module implements a memory-efficient hybrid loading strategy to resolve Out of Memory (OOM) build issues under large datasets.

---

## 2. Key Responsibilities

1. Read static JSON export catalogs, item entries, archives, and statistics from the live generation under `data/publish_export/` (resolved through the `current.json` pointer).
2. Manage internationalization (i18n) routing patterns using Astro's native routing features (referencing designs in `references/astro-i18n-starter/` and `references/astro-paper-i18n/`).
3. Render a highly optimized, responsive timeline UI (based on the `astro-sienna` design in `references/astro-sienna/`) to present articles chronologically.
4. Calculate and display estimated reading time for mixed English and CJK text.
5. Provide a language selection dropdown to easily switch between Traditional Chinese, English, and Japanese translations of the same content.
6. Display AI authorship and curation disclosures prominently using source metadata.
7. Validate structural integrity of ingested JSON files at build time to prevent malformed page generation.

---

## 2.1 Local Development Modes

- `npm run dev` / `npm run build` read the default production export root (`data/publish_export/`), resolve the live generation through its `current.json` pointer, and **hard-fail** when the pointer is missing/invalid or any expected export file within the generation is missing or invalid — bad data is never rendered as empty pages or guessed content.
- `npm run dev:fixture` starts the dev server against the committed minimal fixture at `tests/fixtures/publish_export/` — in the publish generation layout (`current.json` plus `generations/2026-07-22T03-00-00Z/`) — for UI work without a production export. The fixture passes the same validation as production data and is never an implicit input to production builds.
- Details: [BUILD_AND_ROUTING_POLICY.md](./BUILD_AND_ROUTING_POLICY.md) section 4.

---

## 3. Boundary Rules

- `site` must remain a downstream-only consumer. It does not perform writes or state transitions on canonical data.
- `site` does not determine or recalculate article slugs. It relies entirely on the stable slugs provided in the export data.
- `site` does not query the LLM translation API or trigger classification. All content is pre-translated and pre-classified upstream.
- `site` must consume `publish_export` with a predictable build-time memory footprint. It must never load full article bodies for listing/index pages, and dynamic route discovery `props` must remain lightweight (containing identifiers only) to prevent OOM errors.

---

## 4. Document Map

- [DATA_HANDOFF_CONTRACT.md](./DATA_HANDOFF_CONTRACT.md): Ingestion guidelines specifying how the site module reads data elements from the `publish_export` outputs.
- [BUILD_AND_ROUTING_POLICY.md](./BUILD_AND_ROUTING_POLICY.md): Technical policies governing build-time generated markdown, git exclusion, routing invariants, and timestamp configurations.
- [DESIGN_PROPOSAL.md](./DESIGN_PROPOSAL.md): Exploratory design notes, visual analysis of template styles, and UI component drafts.
