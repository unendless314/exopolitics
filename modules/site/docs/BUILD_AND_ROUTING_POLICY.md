# Build and Routing Policy: Phasing, Directory, and Timestamp Contracts

This document defines the build process, temporary file management, routing schemes, and display rules for the `site` module.

---

## 1. Phasing Strategy & Invariants

To allow rapid prototyping and layout updates, the data loading architecture follows a two-phase roadmap.

### 1.1 Ingestion Phases
- **Phase 1 (MVP & Active)**: Build-time JSON-to-Markdown Adapter.
  - Converts JSON exports into temporary Markdown files for standard Astro Content Collection parsing.
- **Phase 2 (Production Stable Target)**: Direct JSON Ingestion.
  - Astro parses JSON files directly during build-time dynamic routing and page compilation, bypassing file creation.

### 1.2 Switch-Over Invariants (Immutables)
To ensure that migrating from Phase 1 to Phase 2 does not break layouts, search engine placement, or functionality, the following attributes must remain unchanged:
1. **Routing Scheme**: The URL pattern `/[lang]/posts/[slug]/` must be preserved exactly.
2. **SEO Metadata Schema**: The structure of header elements (`title`, `description`, `canonical`, `hreflang` alternate links) must be identical.
3. **UI Component Interfaces**: Visual components (Header, Footer, Timeline, LanguageSelector) must consume identical data structures.
4. **Attribution and Disclosures**: The presentation of the AI disclosure note (`disclosure_note`) and original source link (`canonical_url`) must remain consistent.

---

## 2. Phase 1: Generated Markdown Rules

To prevent build-time artifacts from cluttering the codebase, the following rules apply:

### 2.1 Output Directory
- **Path**: `modules/site/src/content/posts/generated/[lang]/[slug].md`
- **Astro Collection Mapping**: Fits the `./src/content/posts` base directory. The glob loader pattern `**/*.{md,mdx}` automatically matches files under the `generated/` subfolder.
- **Git Exclusion**: The directory `modules/site/src/content/posts/generated/` must be added to the `.gitignore` file and never tracked.

### 2.2 Cleanup and Rebuild Policy
- **Execution Rule**: Prior to starting the local development server (`npm run dev`) or compiling the production bundle (`astro build`), the directory `src/content/posts/generated/` must be fully deleted and recreated from scratch.
- **No Manual Edit**: Developers must never edit any files inside `src/content/posts/generated/`. They are treated strictly as transient artifacts derived from `data/publish_export/`.

---

## 3. Timestamp Precision and Display Policy

Timestamps are critical for providing a real-time, high-urgency aesthetic for news alerts.

### 3.1 Display Precision
- **Absolute Precision**: The site displays publication timestamps accurate to the minute (e.g. `2026-06-25 16:07` or `Jun 25, 2026, 16:07`).
- **Standard Layout**: Renders the absolute local timezone date and time.
- **Astro Component Syntax**:
  ```astro
  <time datetime={post.source_published_at}>
    {formatAbsoluteDateTime(post.source_published_at)}
  </time>
  ```

### 3.2 Hydration Rationale
- **Static First**: To maintain a pure Static Site Generation (SSG) architecture with a 100/100 performance score, **no relative time calculations (e.g., "5 minutes ago") requiring client-side hydration scripts will be used**.
- **Performance**: High-precision absolute dates are computed at build time, ensuring zero client-side JavaScript execution overhead for date displays.
