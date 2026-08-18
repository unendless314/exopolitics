# `api` Module Proposal

**Status:** Proposal v1.9 — six external review rounds incorporated. **Phase A (mechanical split of publish's orchestrator) is completed and passed independent code review (2026-08-17)**. **Phase B1 (generation + atomic pointer) landed 2026-08-18** — the publish export is now generation + `current.json` pointer based, and the site-side consumption (resolver, pointer-driven "last updated", generationized fixture) landed with it. Phase B2 (hardlink reuse optimization) remains a separate pending batch. This module stays at documentation stage and is not implemented. Refactor basis: `known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` v7.
**Proposed:** 2026-08-17
**Reversal plan:** delete `modules/api/` entirely. No top-level docs (including `docs/MODULE_BOUNDARIES.md`) have been modified; no other module is affected.

---

## 1. Purpose

Provide a read-only query layer so that AI agents can answer "what is worth reading today" without crawling the rendered site page by page.

Primary consumer: a client-side deep-reader assistant (see the deep-reader strategy notes) that needs the daily approved article list before deciding which original sources to fetch in full.

This module is a **downstream-only consumer of `publish` outputs**, positioned as a sibling of `site`:

```
publish export ──> site  (for humans)
              └─> api   (for the deep-reader agent)
```

Both consumers read only publish-layer outputs; neither touches canonical storage.

**Interim note:** the deep-reader use case is not blocked while this module waits — an agent on the same machine can read `data/publish_export/current.json` and the live generation's `<lang>/index.json` directly with file tools. The API adds contractual stability and freshness semantics, not basic capability.

## 2. Product Constraint (owner decision, 2026-08-17)

The site is a breaking-news aggregator with strong timeliness. Deep-reader queries are almost always about the recent past ("today", "the day before", "this week so far"); content older than roughly a month is permanently archived and has no query demand.

Two consequences:

1. **Event-time semantics.** Filtering and sorting use `source_published_at` (when the external source published the item) — the only export timestamp referring to the external world. Internal processing timestamps (`approved_at`, `published_at`, `upstream_updated_at`) are reference-only.
2. **Recent-window scope.** v1 reads `index.json` (latest 1,000 items, sorted by `source_published_at` descending, ~34 days of event time at verification) plus per-slug joins into `items/`. No full-set scan, no archives stitching. See `API_CONTRACT.md` §3–§5.

## 3. Rationale

### 3.1 Why a standalone module instead of static JSON emitted by `publish`

A cheaper alternative — having `publish` emit extra API-shaped JSON files during export — was considered and rejected:

- `publish` owns export emission for **site content presentation**. Letting it also own the agent-consumption contract gives it two external contracts for two different audiences.
- When a real API layer is eventually needed, those responsibilities would have to be carved back out of `publish`, causing cross-module refactoring — boundary debt.
- The repo already has precedent for read-only downstream modules: `analysis` (read-only sidecar observer) and `dashboard` (pure presentation consumer of `analysis` JSON). `api` follows the same pattern.

### 3.2 Why plain REST/JSON instead of MCP

The use case is unidirectional read-only pulls. MCP's tool discovery, stateful sessions, and cross-client standardized tool semantics are not needed at this stage, and an MCP server adds lifecycle and protocol maintenance burden. If the MCP ecosystem matures into a real requirement later, a thin MCP shim can wrap the same data contract without discarding this work.

A `llms.txt` at the site root declaring the endpoints may be added later as a low-cost agent-adaptability measure.

### 3.3 Scope discipline

v1 serves the deep-reader agent only. It is deliberately **not** designed as a general-purpose content platform; fields not needed by the deep-reader (`bullets`, `disclosure_note`, `author_metadata`, `category`) are excluded, and historical-query capability (archives) is explicitly out of scope. Generalization is deferred until a second consumer exists.

### 3.4 Why publish-refactor-first (owner architecture decision, 2026-08-17)

Review round 2 surfaced that export reads are unsafe without a generation marker, and offered two fixes: a reader-side defense (manifest-bracketed reads with retry) or a writer-side guarantee (immutable generation directories + atomic pointer). The owner, weighing long-term system health and AI-implementation token economics, chose **writer-side first**:

- The reader-side retry protocol would become dead code the moment the writer-side fix lands — two paid implementation passes for one adapter.
- The writer-side fix deletes complexity in publish itself (per-file promote, per-file backups, promotion journal, withdrawal/language-shrink sweeps all simplify) and benefits every current and future consumer of the export, including `site`.
- The pattern already has an accepted pending precedent in this repo: `known_issues/SITE_RELEASE_POINTER_PROMOTION_PROPOSAL.md`.

Consequence: **api implementation was gated on the publish refactor, by design and by preference.** Phase B1 — the part that establishes the reader-visible contract this module depends on — has since landed (2026-08-18); Phase B2 (hardlink reuse optimization) remains pending as an independent batch that does not change generation immutability or the reader protocol. The module stays at documentation stage until the refactor completes and the owner green-lights implementation. The refactor basis lives in `known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` (v7).

## 4. Proposed Boundary Definition

Written to match the format of `docs/MODULE_BOUNDARIES.md` section 3. On approval, this text is intended to become section 3.10 of that document in the same change.

Owns:

- the external read-only contract: endpoints, response schemas, and versioning (`/v1/` prefix)
- query semantics: event-time (`source_published_at`) range filtering, stable ordering, cursor pagination, coverage and freshness declaration
- transport and deployment form (local service, bind address, port)
- response caching policy

May read:

- publish-layer outputs only (`data/publish_export/`, via the `current.json` pointer) — identical consumption scope to `site`

Must not own:

- canonical DB access or writes
- export shape, generation layout, or slug generation (owned by `publish`)
- pipeline execution
- content lifecycle state changes (e.g. withdraw decisions)
- the set of supported languages (authoritative source: `current.json`; directory existence is not evidence)

## 5. Key Design Principle: Adapter Layer

The module exposes **its own** contract externally, but v1 response field names follow the publish-export naming verbatim (`display_title`, `summary_short`, `canonical_url`, ...); the adapter is a thin pass-through and must not rename or re-derive semantics. If `publish` later restructures its export format, only the adapter changes; the `/v1/...` contract seen by agents remains stable.

## 6. Technical Direction

- **Stack:** FastAPI (the pipeline is already Python; `pipeline.sh`, `pytest`)
- **Deployment:** local service bound to `127.0.0.1` only; no authentication required at this stage
- **Data layer:** reads `current.json` → resolves the generation directory → `index.json` + per-slug `items/` joins; no database driver needed
- **First endpoint:** `GET /v1/articles?event_from=...&event_to=...&language=...&limit=...&cursor=...` — full contract in `API_CONTRACT.md`

## 7. Upstream Precondition (met for Phase B1)

**Phase B1 of the publish generation-pointer refactor** (`known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md` v7) landed on 2026-08-18. The assumptions this contract was written against are now live behavior:

1. **Generation consistency:** pre-B1 promotion was per-file and non-atomic, with withdrawal cleanup interleaved. Generation directories are now immutable and `current.json` switches atomically — content drift during a read is impossible by construction. One narrow retry remains: if retention sweeps the resolved generation at any point during the read flow, the reader re-resolves the pointer and re-runs the flow once before returning `503` (see `API_CONTRACT.md` §7).
2. **Language authority:** the pointer's `languages` list is the single source of truth; directory existence is not evidence (publish's own orchestration said so even before the refactor).
3. **Freshness signaling:** the pointer carries `export_completed_at` and `last_successful_run_at`, letting the agent distinguish "no news today" from "export not updated yet".

Phase B2 (hardlink reuse optimization) is still pending as an independent batch; it does not change the reader-visible contract above. The api module itself remains at documentation stage.

Deferred upstream item (not a blocker): `category` in export items — only if a future consumer needs classification in the reading list.

## 8. Open Questions

1. Deployment form: always-on local service vs. on-demand startup?
2. If non-local agents ever need access, what is the authentication strategy?
3. Does read-only access to the export make the deep-reader trigger naturally post-publish (resolving the trigger-timing question in the strategy notes)?
4. Freshness SLA default (`freshness_sla_hours`, currently 48): should it track the actual pipeline cadence once observed?

## 9. Scope of This Proposal

Included now:

- this proposal document
- `API_CONTRACT.md` — v1.8 contract draft rebased onto the generation-pointer layout

Deliberately excluded until the publish refactor completes (Phase B1 landed 2026-08-18; Phase B2 pending) and this proposal is approved:

- changes to any top-level doc
- `src/`, `config/`, `tests/` scaffolds
- any executable code

## 10. On Approval, the Implementation Change Must Also Update

(After the publish refactor completes — Phase B1 landed 2026-08-18, Phase B2 pending; the refactor plan lists its own doc set.)

- `docs/MODULE_BOUNDARIES.md` — add section 3.10 from §4 above
- `docs/SYSTEM_OVERVIEW.md` — add `api` to the module sequence/diagram
- `docs/DATA_LIFECYCLE.md` — record `api` as a downstream consumer of publish exports
- `docs/IMPLEMENTATION_ROADMAP.md` — add the `api` work item
- `AGENTS.md` — module list and ownership rules, if it enumerates modules

