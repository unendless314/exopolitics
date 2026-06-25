# Reference Templates Quality Assessment

## 1. Purpose

This document records a code-quality review of the Astro templates collected under
`references/` as part of the `site` module planning effort. The goal is to inform the
decision about which templates are worth borrowing patterns, code, or styling from, and
which should be excluded from `DESIGN_PROPOSAL.md` and the Sampling Strategy Matrix
(`modules/site/docs/DESIGN_PROPOSAL.md:6.1`).

The review focuses on three dimensions for each template:

1. **Static-site-generation fit** — does the template ingest content from local files,
   or is it bound to an external service / CMS?
2. **Code quality** — type safety, idiomatic Astro patterns, bug surface, dependency
   surface area, accessibility, and i18n correctness.
3. **Reusability** — can discrete pieces (CSS variables, layouts, i18n helpers,
   typography) be transplanted cleanly into our own module without dragging in the
   surrounding stack?

---

## 2. Summary

| Reference | Role in Proposal | Code Quality | Verdict |
| :--- | :--- | :--- | :--- |
| `astro-sienna` | Visual & layout core | High | **Adopt** (with caveats) |
| `astro-i18n-starter` | i18n engineering reference | High | **Adopt** |
| `astro-paper-i18n` | i18n engineering reference | High | **Adopt** (most complete) |
| `astroplate-multilingual` | i18n engineering reference | Low | **Drop** |
| `astro-theme-retypeset` | Prose typography reference | Medium | **Aesthetic-only reference** |
| `bcms-podcast` | Audio conceptual reference | Medium | **Drop** |

Two templates (`astroplate-multilingual`, `bcms-podcast`) are not suitable for adoption.
The remaining four each contribute something useful; `astro-paper-i18n` provides the
strongest foundation for the i18n layer.

All template-specific findings below were re-checked against actual files under
`references/` in this workspace, not just against README-level descriptions.

---

## 3. Per-Template Findings

### 3.1 `astro-sienna` — Visual & Layout Core

**Files reviewed:** `astro.config.ts`, `package.json`, `tailwind.config.ts`,
`src/styles/global.css`, `src/layouts/Base.astro`, `src/layouts/BlogPost.astro`,
`src/data/post.ts`, `src/utils/date.ts`, `src/plugins/remark-reading-time.ts`,
`src/plugins/remark-admonitions.ts`.

**Strengths**

- Clean CSS Grid-based timeline with rail column, dot markers, and hover transitions
  (`src/styles/global.css:129-253`). The grid degrades to a single column under 720px.
- HSL-channel CSS variables (`--theme-bg`, `--theme-text`, `--theme-accent`, etc.) used
  consistently for theme toggling. `--reading-width: 64ch` and `--rail-width` are
  reusable layout tokens.
- `src/utils/date.ts` demonstrates the `Intl.DateTimeFormat` cache pattern with
  per-context formatters (rail / stamp / byline / eyebrow) — directly applicable to
  the proposal's high-precision absolute date requirement.
- Strong accessibility: `prefers-reduced-motion`, `focus-visible` outlines,
  `:has()`-based pagination layout, color contrast across light/dark themes.
- Strict TypeScript with `@/*` path alias; uses Biome for formatting and linting.

**Code evidence checked**

- Tailwind is wired directly into Astro config via `@astrojs/tailwind` with
  `applyBaseStyles: false` (`references/astro-sienna/astro.config.ts:47-49`).
- The visual token layer and timeline layout live in real reusable CSS, not just page
  mockups: `@tailwind` directives appear at the top of
  `references/astro-sienna/src/styles/global.css:8-10`, the token block is at
  `:root` / theme scopes in `:26-72`, and the timeline rules are at `:128-264`.
- Date formatter caching is implemented as module-scope `Intl.DateTimeFormat`
  instances in `references/astro-sienna/src/utils/date.ts:3-25`.

**Caveats**

- The proposal at `DESIGN_PROPOSAL.md:15` describes Sienna as "clean, high-performance
  vanilla styling". This is inaccurate — the template depends on Tailwind
  (`@astrojs/tailwind:6.0.2`, `tailwindcss:3.4.19`, `prettier-plugin-tailwindcss`,
  `tailwind.config.ts`). If "vanilla CSS" is a real preference, Tailwind layers must be
  peeled off when borrowing styles.
- `astro.config.ts:99-105` contains a bug: `rehypeExternalLinks` is configured with
  `rel: ["nofollow, noreferrer"]` (single string containing a comma) instead of
  `rel: ["nofollow", "noreferrer"]`. The plugin expects an array of tokens. Do not copy
  this configuration verbatim.
- `src/plugins/remark-reading-time.ts` uses the `reading-time` npm package, which
  computes words per minute. It does not understand CJK characters. The proposal's
  §4.1 CJK-aware estimator must be authored independently; do not adopt this plugin.
- Heavy dependency footprint relative to our needs: Partytown, KaTeX, expressive-code,
  satori (OG images), sharp, astro-seo-schema, @playform/compress, astro-robots-txt,
  astro-webmanifest. Adopt only the layout, CSS variable system, and date helpers.
- Fonts are Latin-optimized (Newsreader Variable, Inter, JetBrains Mono). CJK glyphs
  will fall back to system fonts; this is acceptable for our use case but should be
  acknowledged.

**Additional implementation note from code review**

- The global stylesheet imports KaTeX and font packages at top level
  (`references/astro-sienna/src/styles/global.css:2-6`). If we borrow this file as a
  starting point, those imports need to be removed early or they will quietly drag in
  typography and math-rendering assumptions unrelated to the `site` module.

**Recommendation**

Adopt selectively. Take the timeline grid, CSS variable layer, `Base.astro` /
`BlogPost.astro` shell structure, and `utils/date.ts` formatter pattern. Strip Tailwind
integrations, do not copy `remark-reading-time`, and fix the `rel` array bug before
reusing `rehype-external-links` config.

### 3.2 `astro-i18n-starter` — i18n Engineering Reference

**Files reviewed:** `package.json`, `astro.config.mjs`, `src/i18n.ts`, `src/locales.ts`,
`src/components/i18n/*`.

**Strengths**

- Minimal dependency surface: Astro 7 native i18n plus `@astrojs/mdx`, `@astrojs/rss`,
  `@astrojs/sitemap`. No third-party i18n libraries.
- Clean translation helper (`src/i18n.ts:45-53`): `useTranslations(lang)` returns a
  closure `t(multilingual)` that accepts either a string or a `Multilingual` object and
  falls back through `lang → DEFAULT_LOCALE → ""`. Directly maps onto the proposal's UI
  string dictionary requirement.
- `getLocalePaths(url)` (`src/i18n.ts:61-68`) produces locale-switcher paths by combining
  `getRelativeLocaleUrl` from `astro:i18n` with a regex that strips the current locale
  prefix. This is exactly the logic the proposal's §4.3 `LanguageSelector` needs.
- `getLocaleParams(url)` (`src/i18n.ts:81-87`) returns `getStaticPaths` params, useful
  for our `[lang]/...` page structure.
- Locale configuration (`src/locales.ts`) is a single declarative object keyed by
  URL-prefix, with optional `lang` (BCP-47) and `dir` (ltr/rtl).

**Code evidence checked**

- Astro native i18n is configured directly from locale settings in
  `references/astro-i18n-starter/astro.config.mjs:9-16`.
- Sitemap alternate-language metadata is derived from the same locale map in
  `references/astro-i18n-starter/astro.config.mjs:19-28`, which reduces config drift.
- The translator closure and locale-path helper are exactly as described in
  `references/astro-i18n-starter/src/i18n.ts:45-68`.

**Caveats**

- The regex `/^\/[a-zA-Z-]+/` used to strip the locale prefix will not match BCP-47
  subtags that include digits (e.g. `zh-Hant`). Our scope (`zh`, `en`, `ja`) is safe,
  but any future expansion should revisit this.
- `LOCALES_SETTING` uses lowercase keys as URL prefixes; if the publishing contract
  later needs to align URL prefixes with BCP-47, the `defaultLocale` key must match
  exactly.
- Translation typing uses an open `Multilingual` shape (`{ [key in Lang]?: string }`)
  which permits missing keys. Fine for UI strings, but if schema enforcement becomes
  necessary we should switch to a stricter union type per key.

**Additional implementation note from code review**

- `getLocaleParams(url)` in `references/astro-i18n-starter/src/i18n.ts:81-86` is only a
  partial fit for our site, because it infers static params from file names. Useful as a
  pattern for simple localized pages, but not directly reusable for `publish_export`
  driven `[slug]` routes.

**Recommendation**

Adopt. The minimal dependency footprint and direct mapping to the proposal's i18n
config make this a clean base. Prefer this template's `useTranslations` shape for UI
strings, but consider adopting `astro-paper-i18n`'s structured `LocaleProfile` for
locale metadata (see §3.3).

### 3.3 `astro-paper-i18n` — i18n Engineering Reference (recommended base)

**Files reviewed:** `package.json`, `astro.config.ts`, `src/i18n/config.ts`,
`src/i18n/utils.ts`, `src/i18n/types.ts`.

**Strengths**

- The only reference template with **unit tests** for the i18n layer
  (`src/i18n/config.test.ts`, `src/i18n/utils.test.ts`). This is a meaningful quality
  signal.
- `LocaleProfile` (`src/i18n/config.ts:6-13`) bundles `name`, `messages`, `langTag`,
  `direction`, and `googleFontName` per locale into a single typed object. This is
  richer than `astro-i18n-starter`'s flat `LOCALES_SETTING` and is better suited to
  our needs (we will need `langTag` for `Intl.DateTimeFormat`, `direction` for RTL
  readiness, and `name` for the language picker).
- `translateFor(locale)` (`src/i18n/utils.ts:12-32`) returns a translator closure with
  `{key}` substitution support. Cleaner than string concatenation.
- `getRelativeLocalePath` and `stripBaseAndLocale` (`src/i18n/utils.ts:58-90`) handle
  trailing-slash normalization and base path stripping — useful if the site is ever
  hosted under a subpath.
- Dependency-injected helpers (e.g. `_isLocaleKey` and `_getLocaleMsgs` parameters
  with default values) make the utilities testable in isolation.
- Three working locale examples (ar/zh/en) including RTL handling and Chinese
  translation files.

**Code evidence checked**

- The richer locale metadata object is concretely defined in
  `references/astro-paper-i18n/src/i18n/config.ts:6-56`.
- The translation helper, locale guards, base-path aware path helpers, and slug
  stripping logic are implemented in
  `references/astro-paper-i18n/src/i18n/utils.ts:12-113`.
- Translation key typing is strict and exhaustive in
  `references/astro-paper-i18n/src/i18n/types.ts:1-96`, unlike the open record style of
  `astro-i18n-starter`.
- Test coverage is real, not aspirational: config behavior is asserted in
  `references/astro-paper-i18n/src/i18n/config.test.ts:10-75`, and locale-path /
  translator behavior is asserted in
  `references/astro-paper-i18n/src/i18n/utils.test.ts:52-332`.

**Caveats**

- Tailwind 4 via `@tailwindcss/vite`; OG image generation via `satori` + `@resvg/resvg-js`;
  these are not relevant to our build and should be excluded.
- Uses `lodash.kebabcase` and `luxon`. The former can be replaced with a one-liner
  regex; the latter is unnecessary given we already plan `Intl.DateTimeFormat` for date
  formatting.
- `googleFontName` field in `LocaleProfile` is coupled to OG image generation. If we do
  not generate OG images per-locale, this field becomes dead weight.

**Additional implementation note from code review**

- The test suite is a genuine strength, but it also shows that this template has already
  encoded behavior around root-vs-subpath hosting and trailing slash normalization
  (`references/astro-paper-i18n/src/i18n/utils.test.ts:114-332`). That is valuable to
  borrow precisely because our own routing docs already care about stable path
  invariants.

**Recommendation**

Adopt as the primary i18n foundation. Take `src/i18n/config.ts` and `src/i18n/utils.ts`
patterns, plus the `LocaleProfile` shape (omitting `googleFontName`). Add unit tests in
`modules/site/tests/` mirroring the structure of `src/i18n/*.test.ts`. The Vitest test
approach is worth preserving even at the small scale of our locale count.

### 3.4 `astroplate-multilingual` — i18n Engineering Reference (drop)

**Files reviewed:** `package.json`, `astro.config.mjs`, `src/lib/utils/languageParser.ts`,
`src/lib/utils/readingTime.ts`, `src/config/language.json`.

**Issues**

- **Race condition bug** in `src/lib/utils/languageParser.ts:5-15`:

  ```ts
  const locales: { [key: string]: any } = {};
  languagesJSON.forEach((language) => {
      const { languageCode } = language;
      import(`../../config/menu.${languageCode}.json`).then((menu) => {
          import(`../../i18n/${languageCode}.json`).then((dictionary) => {
              locales[languageCode] = { ...menu, ...dictionary };
          });
      });
  });
  const languages = Object.keys(locales); // executes before promises settle
  ```

  `Object.keys(locales)` runs synchronously at module load while the dynamic
  `import().then()` chains are still pending. In practice `languages` will be an empty
  array during static build evaluation, breaking any caller that iterates it.

- **Reading-time bug** in `src/lib/utils/readingTime.ts:8-13`. The implementation
  counts whitespace-separated tokens: `content.split(" ").filter(...)`. CJK text has no
  inter-word spaces, so a Chinese article will register as a single token and produce
  a meaningless reading-time estimate. This is precisely the failure mode our proposal
  §4.1 aims to avoid.

- **Type erasure**: `src/lib/utils/languageParser.ts:6` uses `{ [key: string]: any }`
  for the locales map, removing TypeScript's main benefit at the boundary most likely
  to harbor bugs.

- **Reinvented wheel**: `src/lib/utils/languageParser.ts:77-115` (`slugSelector`)
  re-implements `getRelativeLocaleUrl` from `astro:i18n` by hand, including trailing
  slash logic. This will drift from Astro's own routing behavior.

- **Dependency and stack bloat** for our use case: React 19, `@astrojs/react`,
  Tailwind 4 with `@tailwindcss/forms` and `@tailwindcss/typography`, Bootstrap Grid
  via `tailwind-bootstrap-grid`, `astro-swiper`, `@justinribeiro/lite-youtube`,
  `disqus-react`, `@digi4care/astro-google-tagmanager`, `gray-matter`, `marked`,
  `turndown`, `node-html-parser`, `astro-auto-import`, and four separate build-phase
  generator scripts (`themeGenerator.js`, `jsonGenerator.js`, `llmsGenerator.js`,
  `removeDarkmode.js`, `removeMultilang.js`). This is a marketing-template site
  boilerplate, not a content-pipeline ingestion site.

- **Suspicious version pins**: `package.json:64` declares `"typescript": "^6.0.3"` and
  `package.json:45` declares `"vite": "^8.0.16"`. These majors did not exist as of
  early 2026 and suggest the manifest was authored with placeholder or future-version
  strings. Worth verifying against any actual install before trusting the rest of the
  manifest.

- **Mismatch with our pipeline**: the template assumes content authored in
  `src/content/<contentDir>/` (one subdirectory per locale, configured in
  `language.json`). Our pipeline ingests `data/publish_export/<lang>/items/<slug>.json`
  emitted by the `publish` module. There is no overlap in ingestion shape to learn
  from.

**Code evidence checked**

- The race condition is present exactly as described in
  `references/astroplate-multilingual/src/lib/utils/languageParser.ts:5-18`: async
  `import()` chains populate `locales`, but `Object.keys(locales)` is captured
  synchronously before any promise settles.
- `getLangFromUrl()` then relies on `locales.hasOwnProperty(lang)` in
  `references/astroplate-multilingual/src/lib/utils/languageParser.ts:23-29`, so the
  empty `locales` state can leak into runtime behavior.
- The whitespace-token reading-time logic is implemented in
  `references/astroplate-multilingual/src/lib/utils/readingTime.ts:8-13`.
- The locale/content model is configured around per-language content directories in
  `references/astroplate-multilingual/src/config/language.json:1-14`.
- The package manifest really does pull in the broader mixed stack called out above,
  including React, Astro React, Swiper, Disqus, multiple generator scripts, and the
  suspicious `vite` / `typescript` version ranges in
  `references/astroplate-multilingual/package.json:10-20` and `:22-65`.

**Recommendation**

**Drop.** The template has at least two concrete bugs that conflict with our CJK
estimator requirement and with idiomatic Astro i18n usage. Its architecture is
significantly heavier than what our static-export pipeline needs. Remove references to
this template from `DESIGN_PROPOSAL.md` §1.2 and §6.1.

### 3.5 `astro-theme-retypeset` — Prose Typography Reference

**Files reviewed:** `package.json`, `astro.config.ts`, `uno.config.ts`,
`src/i18n/config.ts`, `src/i18n/path.ts`, `src/plugins/remark-reading-time.mjs`.

**Strengths**

- Excellent prose styling with attention to vertical rhythm, heading anchor
  permalinks, decorative rules, and code block copy buttons.
- `uno.config.ts:62-69` defines a `cjk:` variant selector targeting
  `:lang(zh), :lang(ja), :lang(ko)`:

  ```ts
  variants: [
      (matcher) => {
          if (!matcher.startsWith('cjk:')) return matcher
          return {
              matcher: matcher.slice(4),
              selector: s => `${s}:is(:lang(zh), :lang(ja), :lang(ko))`,
          }
      },
  ],
  ```

  This is a useful pattern for adjusting spacing, font weight, or letter-spacing for
  CJK runs even when our primary CSS system is not UnoCSS. We can express the same
  idea with vanilla `[lang|=zh], [lang|=ja] { ... }` selectors.
- `src/i18n/config.ts` cleanly separates URL-prefix (`path`) from BCP-47 (`codes`) and
  supports a `zh` / `zh-tw` split. If our `translate` module later needs to distinguish
  Traditional and Simplified Chinese for downstream consumers, this pattern is worth
  keeping in mind.
- `src/i18n/path.ts` provides robust path helpers (`getTagPath`, `getPostPath`,
  `getLocalizedPath`, `getNextLangPath`) that handle base path stripping and trailing
  slash normalization. Useful reference even if we do not adopt UnoCSS.

**Code evidence checked**

- The `cjk:` variant is implemented exactly in
  `references/astro-theme-retypeset/uno.config.ts:60-69`.
- The language config really does separate URL keys from BCP-47-ish codes in
  `references/astro-theme-retypeset/src/i18n/config.ts:2-14`.
- Path helpers covering localized route construction and language switching are in
  `references/astro-theme-retypeset/src/i18n/path.ts:12-112`.
- The reading-time plugin is another thin wrapper over `reading-time` in
  `references/astro-theme-retypeset/src/plugins/remark-reading-time.mjs:1-10`.

**Caveats**

- Uses **UnoCSS** (presetWind3 + presetAttributify + presetTheme) rather than vanilla
  CSS or Tailwind. Another example of the proposal's "vanilla styling" language not
  matching the reference reality.
- Integration stack far exceeds our scope: Mermaid, KaTeX, Waline, Twikoo, Giscus,
  `sanitize-html`, `markdown-it` (replacing Astro's native markdown rendering),
  `astro-og-canvas` with `canvaskit-wasm` (heavy OG image generation), `lite-youtube-embed`,
  `autocorrect-node` (text autocorrection at build time).
- `src/plugins/remark-reading-time.mjs` uses the same `reading-time` npm package as
  Sienna — no CJK awareness.
- LQIP preprocessing scripts (`scripts/apply-lqip.ts`, `scripts/format-posts.ts`,
  `scripts/update-theme.ts`, `scripts/new-post.ts`) target an authoring workflow with
  image-heavy posts. Our content is text-first RSS-style aggregation.
- Custom `markdown-it` pipeline bypasses Astro's native markdown styles, which
  conflicts with the proposal's §6.1 rule: "Rely on Astro's native markdown styles
  ... ensuring that plugins (syntax highlighting, TOC, external link behaviors)
  behave identically across the website."

**Additional implementation note from code review**

- The package manifest confirms that this is not merely a typography skin. It carries a
  substantial rendering and authoring stack, including `markdown-it`, Mermaid,
  Waline/Twikoo, `astro-og-canvas`, UnoCSS, and LQIP scripts
  (`references/astro-theme-retypeset/package.json:7-17` and `:19-67`). That reinforces
  the recommendation to borrow aesthetics only.

**Recommendation**

Aesthetic-only reference. Take the prose typography from `src/styles/markdown.css`,
the CJK variant selector concept (re-expressed in vanilla CSS), and the path helpers
in `src/i18n/path.ts` if they prove useful. Do not adopt the integration stack, the
custom `markdown-it` pipeline, or the LQIP / authoring scripts.

### 3.6 `bcms-podcast` — Audio Conceptual Reference (drop)

**Files reviewed:** `package.json`, `README.md`, `src/bcms-private.ts`,
`src/bcms-public.ts`, `src/context/PlayerContext.tsx`.

**Issues**

- **Bound to a commercial headless CMS** (`@thebcms/cli`, `@thebcms/client`,
  `@thebcms/components-react`, `@thebcms/types`, `@thebcms/utils`). Every build script
  begins with `bcms pull types lng ts` (`package.json:6-10`). `src/bcms-private.ts` and
  `src/bcms-public.ts` instantiate BCMS clients at module load. There is no
  static-JSON ingestion path.
- **React Context audio player** (`src/context/PlayerContext.tsx`, 279 lines) is built
  around a playlist queue with prev/next episode navigation, volume persistence,
  progress bar seeking, and time-update event wiring. Our requirement is "if a post
  has an `audio_url` field, show a single floating playback drawer" — a native
  `<audio controls>` element suffices. The React player is overkill and pulls React +
  the entire BCMS integration surface into the build.
- **Type-safety shortcut**: `src/context/PlayerContext.tsx:47` uses
  `createContext<PlayerContextValue>(undefined as unknown as PlayerContextValue)`,
  deferring null-context errors to runtime rather than typing the default correctly.
- **No static-export or JSON-ingestion patterns** are present to learn from. The
  template's value to us is limited to a single UI concept (fixed bottom audio bar).
- The proposal's §4.4 already provides a simpler, correct implementation of that
  single concept.

**Code evidence checked**

- The BCMS coupling is explicit in build scripts and dependencies in
  `references/bcms-podcast/package.json:5-29`.
- Both client instances are created directly from BCMS environment variables in
  `references/bcms-podcast/src/bcms-private.ts:1-6` and
  `references/bcms-podcast/src/bcms-public.ts:1-6`.
- The player context really is a sizable React state machine, not a light wrapper:
  `references/bcms-podcast/src/context/PlayerContext.tsx:16-279` manages queue
  navigation, volume, current time, progress bar math, DOM event listeners, and
  playback state.
- The README itself states that this is a BCMS starter kit rather than a generic audio
  UI reference (`references/bcms-podcast/README.md:1-3`).

**Recommendation**

**Drop.** The template's coupling to BCMS is fundamentally incompatible with our
`publish_export` JSON ingestion model, and the React Context player architecture is
mismatched to a static single-article audio drawer. Remove references from
`DESIGN_PROPOSAL.md` §1.4 and §6.1.

---

## 4. Required Updates to `DESIGN_PROPOSAL.md`

Most of the originally flagged proposal issues have now been corrected. The remaining
guidance below is retained as implementation caution, not as a claim that the current
`DESIGN_PROPOSAL.md` is still missing these edits.

1. **Tailwind adoption scope** — The proposal now correctly describes Sienna as a
   Tailwind-based reference. Keep implementation discipline around what is actually
   borrowed: layout patterns, tokens, and utility usage conventions are fine, but heavy
   optional integrations from the template should still be excluded.

2. **Dropped references stay dropped** — `astroplate-multilingual` and `bcms-podcast`
   have been removed from the active proposal. Keep them out of future sampling edits
   unless new evidence justifies reintroduction.

3. **CJK reading-time implementation caution** — The proposal now points to a custom
   mixed-script estimator. When implemented, avoid importing `reading-time` from Sienna
   or Retypeset and verify behavior on mixed CJK + Latin content, not just monolingual
   samples.

4. **Phase 1 boundary discipline** — The docs now describe generated Markdown as a
   transient compatibility layer. Preserve that constraint during implementation: site
   logic should not accumulate site-owned metadata in generated `.md` files.

5. **Locale-key vs metadata separation** — The proposal now correctly uses `zh` as the
   stable route/export key and `zh-Hant` as metadata. Keep that split intact unless the
   upstream `publish` contract is changed in a coordinated cross-module update.

6. **Build-time validation** — The proposal now mentions fail-fast validation. Keep that
   requirement explicit when implementation tasks are split out so malformed
   `publish_export` data cannot silently degrade into guessed UI output.

---

## 5. Recommended Adoption Set

Concretely, the patterns worth transplanting into `modules/site/`:

| Concern | Source | Files / Patterns |
| :--- | :--- | :--- |
| Visual baseline, CSS variable layer, typography rhythm | `astro-sienna` | `src/styles/global.css`, `src/layouts/Base.astro` (with Tailwind stripped) |
| Timeline grid | `astro-sienna` | `src/styles/global.css:129-253` |
| Date formatting helpers | `astro-sienna` | `src/utils/date.ts` |
| Locale metadata shape | `astro-paper-i18n` | `src/i18n/config.ts` (`LocaleProfile`) |
| i18n helpers with DI for testability | `astro-paper-i18n` | `src/i18n/utils.ts` |
| UI string translation closure | `astro-i18n-starter` | `src/i18n.ts:45-53` (`useTranslations`) |
| Locale switcher path generation | `astro-i18n-starter` | `src/i18n.ts:61-68` (`getLocalePaths`) |
| Prose typography details | `astro-theme-retypeset` | `src/styles/markdown.css` (concept only) |
| CJK variant selector | `astro-theme-retypeset` | `uno.config.ts:62-69` (re-expressed in vanilla CSS) |

If the documentation engineer prefers a single template to mirror rather than mix-and-match,
`astro-paper-i18n` is the strongest candidate: it has the cleanest i18n layer, includes
unit tests, and is the closest match to our locale count and shape.

---

## 6. Resolved Decisions from Open Questions

The open questions raised during documentation planning have been resolved as follows:

1. **Styling Approach**: Tailwind CSS v3 is adopted for the MVP to accelerate styling iteration and simplify template code reuse, with a long-term goal of refactoring/peeling utility classes back to Vanilla CSS in a post-MVP phase.
2. **Default Locale Key**: The stable route and export key is `zh` (Traditional Chinese) to match the upstream export directories, while BCP-47 traditional Chinese metadata is mapped to `zh-Hant` for HTML rendering, SEO, and date utilities.
3. **Audio Narration Feature**: Dropped from the MVP scope to keep the initial development footprint minimal and focus on readable content.
4. **Temporary Markdown Generation**: Retained for the MVP phase. Converting JSON to generated Markdown files allows us to leverage Astro's native Content Collections and easily swap reference themes. Direct JSON ingestion is deferred to Phase 2.
5. **Locale Metadata Source**: Sourced canonically from a dedicated helper in `src/utils/i18n.ts` rather than hardcoded in individual presentation components.

---

## 7. Cross-Document and Planning Concerns

The following items are intentionally separated from the per-template findings above.
They are not claims about bugs in the reference templates themselves; they are concerns
about how the current `site` planning docs interpret or operationalize those references.

### 7.1 Additional Cross-Document Corrections

The earlier `README.md` inconsistencies around `astroplate-multilingual` and
`bcms-podcast` have now been corrected. Remaining cross-document attention should focus
on keeping locale-key routing (`zh` / `en` / `ja`) distinct from richer locale metadata
(`zh-Hant`, `en-US`, `ja-JP`) unless the upstream `publish` contract is intentionally
changed in a coordinated cross-module update.
