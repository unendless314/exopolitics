# Site Module

**Document version:** v1.0  
**Updated:** 2026-06-25  
**Status:** Active draft

---

## 1. Module Positioning

`site` is the final, downstream presentation module in the active aggregation pipeline:

`ingest -> classify -> curate -> edit (when needed) -> translate -> publish -> site`

The module reads static, pre-compiled JSON files written by the `publish` module and compiles them into a static web application using Astro.

In the current architecture:
- `site` does not read from canonical operational database tables directly.
- `site` does not write to the canonical database.
- `site` owns only UI layouts, styles, dynamic routing patterns, UI localization (i18n), and SEO elements.

---

## 2. Key Responsibilities

1. Read static JSON export catalogs, item entries, archives, and statistics from `data/publish_export/`.
2. Manage internationalization (i18n) routing patterns using Astro's native routing features (referencing designs in `references/astro-i18n-starter/`, `references/astro-paper-i18n/`, and `references/astroplate-multilingual/`).
3. Render a highly optimized, responsive timeline UI (based on the `astro-sienna` design in `references/astro-sienna/`) to present articles chronologically.
4. Calculate and display estimated reading time for mixed English and CJK text.
5. Provide a language selection dropdown to easily switch between Traditional Chinese, English, and Japanese translations of the same content.
6. Display AI authorship and curation disclosures prominently using source metadata.
7. Support an optional floating audio narration player layout ready for future voice read-aloud services (referencing player layout structure in `references/bcms-podcast/`).

---

## 3. Boundary Rules

- `site` must remain a downstream-only consumer. It does not perform writes or state transitions on canonical data.
- `site` does not determine or recalculate article slugs. It relies entirely on the stable slugs provided in the export data.
- `site` does not query the LLM translation API or trigger classification. All content is pre-translated and pre-classified upstream.

---

## 4. Document Map

- [DATA_HANDOFF_CONTRACT.md](./DATA_HANDOFF_CONTRACT.md): Ingestion guidelines specifying how the site module reads data elements from the `publish_export` outputs.
- [BUILD_AND_ROUTING_POLICY.md](./BUILD_AND_ROUTING_POLICY.md): Technical policies governing build-time generated markdown, git exclusion, routing invariants, and timestamp configurations.
- [DESIGN_PROPOSAL.md](./DESIGN_PROPOSAL.md): Exploratory design notes, visual analysis of template styles, and UI component drafts.
