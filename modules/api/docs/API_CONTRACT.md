# `api` Module — v1 Contract Draft

**Status:** Draft v1.8 — the publish refactor is complete: Phase B1 landed 2026-08-18 (generation + `current.json` pointer based export, consumed by the site through the pointer) and Phase B2 (hardlink reuse) landed 2026-08-22 without changing the reader-visible contract. Refactor basis: `known_issues/resolved/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` v7. The api module itself remains at documentation stage and is **not implemented**.
**Updated:** 2026-08-22

**Scope:** v1 serves the deep-reader agent only. It is deliberately not designed as a general-purpose content API; generalization decisions are deferred until a second consumer exists.

**Product constraint (owner decision, 2026-08-17):** the site is a breaking-news aggregator with strong timeliness. Deep-reader queries are almost always about the recent past ("today", "the day before", "this week so far"). Content older than roughly a month is permanently archived and has no query demand. v1 is scoped to the recent window only — this is a product definition, not a limitation.

---

## 1. Conventions

- All endpoints are prefixed with `/v1/`.
- All responses are JSON, UTF-8.
- The service is read-only: only `GET` is supported in v1.
- Dates use `YYYY-MM-DD`; timestamps use ISO 8601 UTC.
- "Today" means the current UTC date.
- Response field names follow the publish-export naming verbatim (`display_title`, `summary_short`, `canonical_url`, ...). The adapter is a thin pass-through; it must not rename or re-derive semantics.

## 2. What "published" Means

v1 has **no `status` parameter**. An item's presence in the current export generation already implies a conjunction of upstream conditions: curation-approved, translation completed for the language, fingerprint match, language coverage satisfied, and not withdrawn. Re-exposing a single `status=approved` filter would misrepresent that semantics.

If consumers ever need to query non-published states, that requires a formal extension of the publish export contract — out of scope for v1.

## 3. Time Semantics: Event Time, Not Processing Time

v1 filters and sorts by **`source_published_at`** — the time the external source published the item (from feed metadata). This is the only timestamp in the export that refers to the external real world, and it is what a reader means by "today's news".

The other timestamps are internal processing times and are **not** used for filtering:

- `approved_at` — when curation approved the item
- `published_at` — when the item entered the export
- `author_metadata.upstream_updated_at` — when the upstream module last touched the record (also internal; typically equals `approved_at`)

Consequences, both intended:

- An old article approved today (e.g. a 2010 source item approved this week) does **not** appear in "this week" queries — it is not this week's news.
- A recent event approved late (event today, approved tomorrow) **is** correctly captured by event-time queries.

## 4. Endpoint: List Articles

```
GET /v1/articles?event_from=YYYY-MM-DD&event_to=YYYY-MM-DD&language=<code>&limit=100&cursor=...
```

### Query parameters

| Parameter | Required | Default | Notes |
|:---|:---:|:---|:---|
| `event_from` | no | today (UTC) | inclusive lower bound on `source_published_at` date |
| `event_to` | no | today (UTC) | inclusive upper bound on `source_published_at` date |
| `language` | no | `zh` | must be listed in `current.json` (§6); unsupported codes return `400` with the supported list |
| `limit` | no | `100` | max `500` |
| `cursor` | no | — | opaque pagination token from a previous response |

### Response

```json
{
  "range": { "event_from": "2026-08-16", "event_to": "2026-08-17" },
  "language": "zh",
  "coverage": {
    "window_from": "2026-07-02T15:24:00Z",
    "window_to": "2026-08-05T10:10:58Z",
    "basis": "index",
    "generation": "2026-08-05T15-23-51Z",
    "export_completed_at": "2026-08-05T15:23:51Z",
    "last_successful_run_at": "2026-08-17T08:05:11Z",
    "request_exceeds_window_to": true,
    "data_may_be_stale": false,
    "items_without_event_time": 0
  },
  "total_count": 0,
  "returned_count": 0,
  "next_cursor": null,
  "articles": []
}
```

### Field definitions

| Field | Source | Notes |
|:---|:---|:---|
| `source_item_id` | full item record (`items/`) | stable numeric identifier; obtained by joining on `slug` |
| `slug` | index entry | publish-frozen; also the item filename key and join key |
| `display_title` | index entry / item record | translated, per `language` |
| `summary_short` | index entry / item record | translated, per `language` |
| `canonical_url` | index entry / item record | the original source URL the deep-reader fetches |
| `source_published_at` | index entry | **event time** — basis for filtering and primary sort |
| `approved_at` | index entry | internal; reference only |
| `published_at` | index entry | internal; reference only |
| `downstream_action` | full item record (`items/`) | e.g. `publish_summary`, `publish_link`; tells the agent whether richer readable content exists or only the link |

Deliberately excluded in v1: `category` (not present in the export; adding it requires a formal publish export contract extension), `bullets`, `disclosure_note`, `author_metadata` (not needed by the deep-reader).

### Ordering and pagination

- Sort: `source_published_at` descending; tie-breaker: `slug` descending (index entries carry no `source_item_id`, so `slug` is the stable tie-breaker at this layer). This makes ordering total and stable.
- `total_count` = number of items matching the filter within the coverage window (all pages); `returned_count` = items in this response.
- `cursor` is an opaque token encoding **the generation** plus the last `(source_published_at, slug)` of the previous page. `next_cursor` is `null` on the final page.
- **Cursors are generation-bound.** If the pointer's generation differs from the cursor's generation (a new generation was published between pages), the request fails with `400` and error code `cursor_expired`; the client must restart from the first page. The API must never silently mix pages from two generations — that would produce gaps, duplicates, and inconsistent `total_count`.

### Coverage and freshness semantics

- `coverage.window_from` / `coverage.window_to` describe the index window actually searched (§5).
- `coverage.generation` / `coverage.export_completed_at` identify the export generation being served; `coverage.last_successful_run_at` is the pipeline health signal — all three come from `current.json` (§7).
- **Historical gap:** a query range (partially) older than `window_from` is not an error — the API returns matches inside the window. `coverage` tells the agent that older content exists but is out of scope.
- **Content coverage vs. pipeline health are separate signals:**
  - `request_exceeds_window_to` is `true` when the request range extends beyond `window_to` (no items in the window for the requested dates). With a healthy pipeline this can be a legitimate editorial fact: nothing new happened.
  - `data_may_be_stale` is `true` when `last_successful_run_at` is older than the configured freshness threshold (`freshness_sla_hours`, default `48`). This means the **pipeline itself** may not have run — independent of whether content changed. (A successful run refreshes `last_successful_run_at` even when no content changed; see the refactor plan v7.)
- **Agent guidance (normative):** when `data_may_be_stale` is `true`, the agent must report "the site's pipeline has not run recently" — never "there was no news". When only `request_exceeds_window_to` is `true` and the pipeline is fresh, the agent may report that no new items appeared in the window.
- `coverage.items_without_event_time` counts in-window items skipped because `source_published_at` was missing or unparseable (feed metadata quality varies by source).

### Errors

| Status | Condition |
|:---|:---|
| `400` | malformed date; `event_from` > `event_to`; unsupported `language` (error body lists supported codes from `current.json`); `limit` out of range; invalid `cursor`; `cursor_expired` (cursor generation ≠ current pointer generation — restart from page 1) |
| `503` | export not serveable: `current.json` missing/invalid, or the generation still unresolvable after one full-flow retry (see §7); response includes `Retry-After` |
| `500` | unreadable or malformed export data within a resolved generation |

## 5. Data Source and Adapter Boundary

Responses derive exclusively from the **current generation** under `data/publish_export/` (layout per `known_issues/resolved/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` v7, landed as Phase B1 on 2026-08-18):

```text
data/publish_export/
  current.json                 # atomic pointer; readers' only entry point
  generations/<generation>/
    stats.json
    meta.json                  # optional diagnostics
    <lang>/index.json  items/  archives/
```

Verified export layout facts (2026-08-17, pre-refactor tree; content contracts carry over unchanged):

- `index.json` holds the latest 1,000 items as slim entries, **sorted by `source_published_at` descending**; the window at verification time spanned ~34 days of event time (2026-07-02 → 2026-08-05).
- `archives/archive_YYYY_MM.json` group by `source_published_at` month.
- `items/` holds all full self-contained records, one JSON per slug.

Given the product constraint (recent-window queries only), the v1 adapter:

1. reads `current.json` and resolves the generation directory (§7),
2. validates the requested language against the pointer's `languages` list,
3. reads `<lang>/index.json` (one file, already in event-time order),
4. filters by event-time range and paginates,
5. joins matched slugs against `<lang>/items/<slug>.json` for the full-record fields (`source_item_id`, `downstream_action`).

It must **not** scan `items/` wholesale and must **not** stitch `index.json` + `archives/`. The archives and any derived full-set indexing are out of scope until a second consumer with historical query demand appears.

## 6. Language Support Is Not Hardcoded — and Directories Are Not Evidence

The authoritative source for the supported language set is the **`languages` list in `current.json`**. Directory existence is explicitly **not** authoritative: publish's execution policy (`modules/publish/docs/EXECUTION_POLICY.md` §6.2) states that directory names are not ownership evidence, and residual directories from before a canonical reset may persist. Serving from an unlisted directory risks exposing content that is no longer configured and no longer meets published conditions.

If `current.json` is missing or invalid, the API does not fall back to directory scanning — it returns `503` (§7).

## 7. Read Consistency: Generation Pointer

Phase B1 of the publish refactor has landed (2026-08-18): each content-changing export run produces an **immutable generation directory** and atomically switches `current.json` (single-file `os.replace`, same volume) only after the generation is complete; successful no-change runs atomically refresh `current.json.last_successful_run_at` without a new generation. A forced `rebuild` also switches the generation even when the content fingerprint is unchanged, so any generation difference — not only a fingerprint change — expires cursors (§4).

Read protocol per request — the **entire read flow is wrapped in a single retry scope**, because retention can sweep the resolved generation at any point, not just during resolution:

1. Read `current.json`. Missing/invalid → `503` with `Retry-After` (the export has never completed — bootstrap has not yet established a pointer).
2. Resolve `generations/<generation>/`, read `index.json`, perform the `items/` joins, and assemble the response.
3. If **any** step of 2 fails because the generation directory (or a file within it) has vanished — including mid-join — re-read `current.json` and **re-run the whole flow once** with the new pointer. If it still fails → `503` with `Retry-After`.

Two further consistency rules:

- Because generation directories are immutable after publication, **no mid-read content revalidation exists**. The retry covers exactly one failure mode: the resolved generation being swept by retention at any point during the read. Within a live generation, drift is impossible by construction.
- Pagination does **not** follow a generation switch: cursors pin the generation they were issued from (§4), so a multi-page read never silently mixes two generations. A client that hits `cursor_expired` restarts from page 1 and gets a consistent new series.

The consistency burden lives once in the writer (publish), not in every reader.

## 8. Versioning Policy

- Breaking changes (field removal, type change, semantics change) require a new prefix (`/v2/`); `/v1/` must keep working while any consumer depends on it.
- Additive changes (new optional fields, new optional parameters) may ship within `/v1/`.

