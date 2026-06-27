# Build and Routing Policy: Phasing, Directory, and Timestamp Contracts

This document defines the build process, temporary file management, routing schemes, and display rules for the `site` module.

---

## 1. Phasing Strategy & Invariants

To allow rapid prototyping and layout updates, the data loading architecture follows a three-phase roadmap.

### 1.1 Ingestion Phases
- **Phase 1 (MVP - Historical Baseline)**: Build-time JSON-to-Markdown Adapter.
  - Converts JSON exports into temporary Markdown files for standard Astro Content Collection parsing.
  - Both listing pages and detail pages rely on Content Collections. While simple, loading all posts with full bodies in memory causes memory OOM issues on large datasets.
- **Phase 2 (Production Stable - Hybrid Ingestion / Active Phase)**: Hybrid Ingestion with Build Memory Constraints.
  - The stabilized phase introduced to resolve OOM issues.
  - **Detail pages** continue to use generated Markdown + Content Collections, but `getStaticPaths()` must only return lightweight identifiers in `props`. The post content is dynamically loaded during page rendering.
  - **Listing and Index pages** (such as homepage/timeline, archives) bypass Content Collections and read metadata-only JSON files directly from `publish_export` (e.g., `index.json`, archive monthly JSON files) to avoid loading full article bodies.
- **Phase 3 (Long-term Target)**: Direct JSON Ingestion.
  - Astro parses JSON files directly during build-time dynamic routing and page compilation, bypassing file creation.
  - Detail pages also read JSON files directly via a custom loader or Node.js `fs` reads, completely phasing out generated Markdown files and Astro Content Collections.

### 1.2 Switch-Over Invariants (Immutables)
To ensure that migrating across phases does not break layouts, search engine placement, or functionality, the following attributes must remain unchanged:
1. **Routing Scheme**: The URL pattern `/[lang]/posts/[slug]/` must be preserved exactly.
2. **SEO Metadata Schema**: The structure of header elements (`title`, `description`, `canonical`, `hreflang` alternate links) must be identical.
3. **UI Component Interfaces**: Visual components (Header, Footer, Timeline, LanguageSelector) must consume identical data structures.
4. **Attribution and Disclosures**: The presentation of the AI disclosure note (`disclosure_note`) and original source link (`canonical_url`) must remain consistent.

---

## 2. Phase 2: Build Memory Constraints & Guardrails

To prevent Node.js heap exhaustion (OOM) during static site building on cloud VPS hosts, the site module must adhere to the following memory consumption policies:

### 2.1 Route Props Size Restrictions
- **Lightweight Props**: The `getStaticPaths()` function for any dynamic routes (especially post detail pages) must return a lightweight `props` payload.
- **Forbidden Content**: It is strictly forbidden to pass the entire `CollectionEntry` object, rendered content, AST, or full Markdown body strings in the `props` of any route path.
- **Permitted Fields**: Only lightweight scalar metadata needed to uniquely identify the entry (such as `id`, `slug`, `lang`) may be passed.
- **Deferred Loading**: The full content, markdown text, or entry metadata must be loaded dynamically inside the page component (using `getEntry()` or direct filesystem reads) during the rendering phase, allowing Node.js to garbage collect (GC) unused documents.

### 2.2 Ingestion Segregation for Listing Pages
- **Metadata-Only Ingestion**: Listing, feed, index, and archive pages must not invoke any APIs or collection loaders that load full article content or markdown bodies.
- **Direct JSON Consumption**: These pages must consume pre-filtered, lightweight metadata catalogs (e.g., `publish_export/<lang>/index.json` or monthly archive JSONs) directly.
- **Formatting Adaptation**: Read metadata elements are mapped to the timeline or list components locally, avoiding Astro Content Collection cache overhead for these pages.

### 2.3 Build Concurrency Limit
- **Concurrency Guardrail**: The build configuration in `astro.config.ts` must restrict parallel page rendering.
- **Config Setting**: Explicitly define `build.concurrency` (e.g., set to `4` or less) to limit peak memory spikes and prevent concurrent V8 heap allocations from exceeding host physical RAM.

---

## 3. Transient Markdown Artifact Rules (Phase 1 & Phase 2 Detail Pages)

To prevent build-time artifacts from cluttering the codebase, the following rules apply to temporary Markdown files generated for Content Collections:

### 3.1 Output Directory
- **Path**: `modules/site/src/content/posts/generated/[lang]/[slug].md`
- **Astro Collection Mapping**: Fits the `./src/content/posts` base directory. The glob loader pattern `**/*.{md,mdx}` automatically matches files under the `generated/` subfolder.
- **Git Exclusion**: The directory `modules/site/src/content/posts/generated/` must be added to the `.gitignore` file and never tracked.

### 3.2 Cleanup and Rebuild Policy
- **Execution Rule**: Prior to starting the local development server (`npm run dev`) or compiling the production bundle (`astro build`), the directory `src/content/posts/generated/` must be fully deleted and recreated from scratch.
- **No Manual Edit**: Developers must never edit any files inside `src/content/posts/generated/`. They are treated strictly as transient artifacts derived from `data/publish_export/`.

---

## 4. Timestamp Precision and Display Policy

Timestamps are critical for providing a real-time, high-urgency aesthetic for news alerts.

### 4.1 Display Precision
- **Absolute Precision**: The site displays publication timestamps accurate to the minute (e.g. `2026-06-25 16:07` or `Jun 25, 2026, 16:07`).
- **Standard Layout**: Renders the absolute local timezone date and time.
- **Astro Component Syntax**:
  ```astro
  <time datetime={post.source_published_at}>
    {formatAbsoluteDateTime(post.source_published_at)}
  </time>
  ```

### 4.2 Hydration Rationale
- **Static First**: To maintain a pure Static Site Generation (SSG) architecture with a 100/100 performance score, **no relative time calculations (e.g., "5 minutes ago") requiring client-side hydration scripts will be used**.
- **Performance**: High-precision absolute dates are computed at build time, ensuring zero client-side JavaScript execution overhead for date displays.
