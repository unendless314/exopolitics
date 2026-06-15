# Review Module

**Document version:** v1.3  
**Updated:** 2026-06-15  
**Status:** Planning & Active rewrite draft

---

## 1. Module Positioning

`review` is the third executable module in the processing pipeline:

`ingest -> classify -> review -> edit (when needed) -> publish -> site`

The module reads `classification_result` records that have been classified as `core` or `adjacent`, evaluates their content via an LLM-driven reviewer prompt, and persists the review decisions, editor handoff briefs (required for all non-discarded items), and immediately publishable summary outputs (required only for approved items) in the canonical database.

### Core Architectural Separation (Post-Rewrite V2)
In this revised MVP rewrite:
* **Workflow Decisions:** `review` owns the downstream workflow state through the `review_decision` contract.
* **Separation of Outputs:** Conceptually and structurally, `review` separates workflow decisions (`review_decision`), future human/editor handoff instructions (`editor_brief`), and the public-facing content product (`review_output`).
* **Boundary Rules:** 
  * `review` must **not** write to `edit_draft` or own site-owned draft logic. Instead, it generates an `editor_brief` to decouple `review` from the future `edit` module.
  * The direct consumers of `review_output` are downstream publishing and export layers (such as a temporary, external `publish mock` validation script). The `review` module does **not** render pages or own site rendering logic.
  * `review` does **not** dictate global model prompt design. It executes using a locally managed config that satisfies a strict reviewer prompt contract.
* **Bypass Complex Full-Text Reconstruction:** To preserve MVP speed, `review` operates strictly on the `source_item_text.sanitized_text` provided by the `ingest` module. On-demand scraping and raw payload reconstruction are deferred.

---

## 2. Key Responsibilities

1. **Pending Queue Selection:** Query unreviewed items (or failed items eligible for automatic retry) that have a classification result of `core` or `adjacent`.
2. **LLM Triage & Evaluation:** Evaluate items by executing a local runner that satisfies the prompt contract to determine eligibility for publication based on content quality, sensationalism, and noise.
3. **Structured Handoff & Output Generation:** Generate structured outputs representing the review decision, editorial guidance, and clean normalized text representation.
4. **Link-Only Publishing Support (`publish_link`):** Allow announcements or short-context items to bypass bullet-point summary generation, providing display title normalization, time metadata, a short summary paragraph (excerpt), and source attribution framing to save token costs.
5. **Conditional Persistence:** Persist review decisions for all items, editor briefs for non-discarded items (`publish_link`, `publish_summary`, `edit_rewrite`), and publishable outputs only for approved items (`publish_link`, `publish_summary`).

---

## 3. Document Map

* [DATA_CONTRACT.md](file:///C:/Users/user/documents/derived-work/modules/review/docs/DATA_CONTRACT.md)  
  Defines the database schema for `review_decision`, `editor_brief`, and `review_output`, including table indexes, foreign keys, and retry count tracking.
* [REVIEW_POLICY.md](file:///C:/Users/user/documents/derived-work/modules/review/docs/REVIEW_POLICY.md)  
  Defines editorial criteria for triage (approval vs. rejection), noise filtering rules, and downstream routing guidelines.
* [PROMPT_CONTRACT.md](file:///C:/Users/user/documents/derived-work/modules/review/docs/PROMPT_CONTRACT.md)  
  Defines the model instructions, prompt variables, and the single-pass JSON output schema (strictly excluding runner-side failed states).
* [EXECUTION_POLICY.md](file:///C:/Users/user/documents/derived-work/modules/review/docs/EXECUTION_POLICY.md)  
  Defines the execution orchestration including batch processing, rate-limit handlers, database transaction blocks, and preview options.
* [IMPLEMENTATION_PLAN.md](file:///C:/Users/user/documents/derived-work/modules/review/docs/IMPLEMENTATION_PLAN.md)  
  Outlines the project roadmap broken down into epics, migration scripts, testing strategies, and MVP constraints.

---

## 4. Config Map

* `modules/review/config/prompt_templates.yaml`  
  Stores the active reviewer prompt templates.
* `modules/review/config/model_settings.yaml`  
  Stores Gemini model selection, parameters, and batch execution configs.

---

## 5. Minimal CLI Usage

Run review database migrations:

```text
python -m modules.review.src.cli migrate --db-path data/canonical.db
```

Preview pending items and prompts without saving or calling the model:

```text
python -m modules.review.src.cli run --db-path data/canonical.db --preview-prompts --batch-size 3
```

Run a live review batch:

```text
python -m modules.review.src.cli run --db-path data/canonical.db --batch-size 20
```

Check review queue status and stats:

```text
python -m modules.review.src.cli status --db-path data/canonical.db
```
