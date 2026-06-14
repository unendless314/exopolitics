# Technical Document Proposal v2: Automated Review MVP with Editor Handoff (`review`)

**Document version:** v2.0  
**Updated:** 2026-06-15  
**Status:** Architecture Proposal for Discussion  

---

## 1. Purpose

This document proposes a revised MVP direction for the `review` module.

This version does **not** replace `modules/review/docs/PROPOSAL.md`. It is a parallel discussion draft intended to help the team evaluate a narrower and more modular first implementation.

The main design goal is:

- keep `review` as the workflow decision owner
- allow summary-based public output early for fast visible results
- prepare a clean handoff contract for a future `edit` module without requiring `edit` to exist in MVP

---

## 2. Recommended MVP Position

The recommended MVP pipeline is:

```text
[ingest] -> [classify] -> [review] -> [publish mock] -> [site/mock frontend]
                           |
                           +-> future [edit]
```

Key interpretation:

- `review` is implemented now
- `edit` is acknowledged in the architecture, but can remain unimplemented initially
- `publish mock` can consume review-generated output directly until a real `edit` module exists

This preserves long-term module boundaries while still enabling a fast end-to-end demo.

---

## 3. Core Architectural Decision

The MVP should keep three outputs conceptually separate even if they are generated in a single LLM call:

1. `review_decision`
2. `editor_brief`
3. `review_output`

These represent different responsibilities:

### 3.1 `review_decision`

Answers:

- should this item continue downstream?
- what is the next recommended action?

This is workflow control data.

### 3.2 `editor_brief`

Answers:

- if a future editor handles this item, what guidance should it receive?

This is handoff metadata for later editorial automation or human override.

### 3.3 `review_output`

Answers:

- what publishable material do we already have right now?

This is the current public-facing artifact that a mock publish layer can render immediately.

Important rule:

- `review_output` is not the same thing as `editor_brief`
- publishable summary text and internal editorial guidance should not share a single overloaded field

---

## 4. Why This Direction Is Preferred

This proposal is preferred over a tightly merged `review + edit` MVP for the following reasons:

1. It keeps the top-level module boundaries intact.
2. It still allows a single-pass LLM workflow for cost and latency efficiency.
3. It avoids coupling site-facing summary text with future editor instructions.
4. It makes it easier to add `edit` later without renaming or redefining existing published fields.
5. It supports a fast result path: summary cards, short updates, or ticker-like output can be shown early.

---

## 5. MVP Workflow

Recommended first-pass workflow:

1. Select pending items from `classification_result` where `topic_class IN ('core', 'adjacent')`.
2. Join `source_item` and `source_item_text` to load source metadata and sanitized working text.
3. Submit the item to a reviewer prompt.
4. Parse one structured response that contains:
   - decision fields
   - editor handoff fields
   - immediate publish output fields
5. Persist the structured outputs.
6. Let `publish mock` render items directly from `review_output` when the decision permits publication.

MVP recommendation for decision outcomes:

- `publish_link`
- `publish_summary`
- `reject`

This keeps the state space small and avoids introducing too many stalled intermediate states before `edit` exists.

---

## 6. Decision Model

### 6.1 Recommended Review Status

Suggested durable review status values:

- `approved`
- `rejected`
- `failed`

### 6.2 Recommended Downstream Action

Suggested action values:

- `publish_link`
- `publish_summary`
- `reject`

Interpretation:

- `approved + publish_link`: publish as a source-linked item without generated summary text, but still allow minimal presentation cleanup such as display title normalization, metadata framing, and safe excerpt selection
- `approved + publish_summary`: publish using `review_output`
- `rejected + reject`: stop downstream publication

Deferred for later:

- `hold_for_edit`
- `needs_rewrite`
- `human_review_required`

Those may become useful later, but they should not be introduced into the MVP unless the team is ready to implement the operational behavior behind them.

---

## 7. Proposed Output Contracts

## 7.1 `review_decision`

Minimum suggested fields:

| Field | Type | Notes |
| :--- | :--- | :--- |
| `review_decision_id` | Integer PK | Surrogate key |
| `source_item_id` | Integer Unique FK | One decision per item in MVP |
| `review_status` | Text | `approved`, `rejected`, `failed` |
| `downstream_action` | Text | `publish_link`, `publish_summary`, `reject` |
| `decision_reason` | Text | Short explanation for auditability |
| `model_name` | Text | Active reviewer model |
| `prompt_version` | Text | Reviewer prompt version |
| `reviewed_at` | Text | UTC ISO-8601 timestamp |
| `created_at` | Text | UTC ISO-8601 timestamp |

Purpose:

- this table is the durable answer to whether an item can move forward

## 7.2 `editor_brief`

Minimum suggested fields:

| Field | Type | Notes |
| :--- | :--- | :--- |
| `editor_brief_id` | Integer PK | Surrogate key |
| `source_item_id` | Integer Unique FK | One brief per item in MVP |
| `brief_goal` | Text | High-level editorial objective |
| `target_format` | Text | Example: `link_card`, `ticker`, `short_summary`, `structured_summary` |
| `key_claim` | Text | Main factual claim to preserve |
| `key_evidence` | Text | Evidence summary to retain |
| `required_context` | Text | Important dates, agencies, hearings, etc. |
| `risk_flags` | Text | JSON or delimited text for caution tags |
| `tone_guidance` | Text | Example: neutral, factual, non-sensational |
| `created_at` | Text | UTC ISO-8601 timestamp |
| `updated_at` | Text | UTC ISO-8601 timestamp |

Purpose:

- this table is the future handoff contract from `review` to `edit`
- it should describe the task, not hardcode one specific editor prompt style

## 7.3 `review_output`

Minimum suggested fields:

| Field | Type | Notes |
| :--- | :--- | :--- |
| `review_output_id` | Integer PK | Surrogate key |
| `source_item_id` | Integer Unique FK | One output per item in MVP |
| `display_title` | Text | Clean display title |
| `summary_short` | Text | Short summary paragraph or sentence |
| `bullet_1` | Text | Nullable |
| `bullet_2` | Text | Nullable |
| `bullet_3` | Text | Nullable |
| `source_attribution_note` | Text | Optional attribution or framing note |
| `created_at` | Text | UTC ISO-8601 timestamp |
| `updated_at` | Text | UTC ISO-8601 timestamp |

Purpose:

- this table stores the immediately publishable artifact for the MVP
- `publish mock` can consume this directly when `downstream_action = publish_summary`

---

## 8. Prompt Design Guidance

The reviewer may still use a single LLM call for efficiency, but the output schema should remain structurally separated.

Recommended shape:

```json
{
  "review_decision": {
    "review_status": "approved",
    "downstream_action": "publish_summary",
    "decision_reason": "Long-form reporting with usable evidence and context."
  },
  "editor_brief": {
    "brief_goal": "Produce a concise neutral summary for a source-linked aggregator card.",
    "target_format": "structured_summary",
    "key_claim": "...",
    "key_evidence": "...",
    "required_context": "...",
    "risk_flags": ["speculative_elements"],
    "tone_guidance": "neutral, factual, non-sensational"
  },
  "review_output": {
    "display_title": "...",
    "summary_short": "...",
    "bullet_1": "...",
    "bullet_2": "...",
    "bullet_3": "...",
    "source_attribution_note": "Source link retained for original reporting."
  }
}
```

Important guidance:

- `editor_brief` should describe constraints and priorities
- `review_output` should remain safe to expose downstream to publish and site layers

---

## 9. Full-Text Reconstruction Position

Full-text reconstruction should be treated as a later enhancement, not as an MVP dependency.

Reasoning:

1. The current system already stores durable sanitized working text for downstream use.
2. The ingest layer retains raw payloads only under bounded retention policy.
3. Reconstructing from fetched article pages introduces a separate content extraction problem.

Recommended MVP rule:

- operate on `source_item_text.sanitized_text` by default
- defer raw reconstruction and on-demand scraping until review quality data shows a clear need

This keeps the first implementation smaller and more reliable.

---

## 10. Publish Mock Behavior

`publish mock` can be introduced before a real `publish` module if its role is clearly limited.

Recommended temporary behavior:

1. If `review_status = approved` and `downstream_action = publish_link`
   - render a source-linked card with minimally processed presentation fields
   - allowed shaping may include cleaned display title, source attribution, time metadata, and a safe short excerpt when one is usable
2. If `review_status = approved` and `downstream_action = publish_summary`
   - render `review_output`
3. If `review_status = rejected`
   - do not render downstream

Boundary note:

- this mock layer should be treated as a temporary consumer for validation
- the long-term `publish` module still owns the export contract

---

## 11. Recommended CLI Surface

Suggested minimal CLI shape:

```text
python -m modules.review.src.cli migrate --db-path data/canonical.db
python -m modules.review.src.cli run --db-path data/canonical.db --batch-size 20
python -m modules.review.src.cli run --db-path data/canonical.db --preview-prompts --batch-size 3
python -m modules.review.src.cli status --db-path data/canonical.db
```

Recommended preview behavior:

- show pending item IDs
- show routing expectations
- show the structured reviewer prompt payload
- avoid database writes

---

## 12. Open Questions For Team Review

The following points should be agreed before implementation:

1. Should `editor_brief.risk_flags` be JSON text or a normalized child table?
2. Should `review_output` support both one-paragraph and bullet-only variants in MVP?
3. Should `publish_link` items still require `display_title`, or can they reuse normalized source titles only?
4. Should `review` skip `unknown` classifications entirely, or should some `unknown` items remain eligible for manual inspection later?
5. At what failure threshold should `failed` items become visible for operator review?

---

## 13. Recommendation Summary

Recommended implementation stance:

1. Build `review` now.
2. Keep `edit` in the architecture, but do not require it for MVP.
3. Separate decision data, editor handoff data, and public-facing output data.
4. Let a temporary `publish mock` consume `review_output` directly so the site can show visible results early.
5. Defer full-text reconstruction and richer editorial states until the MVP proves where they are actually needed.

This approach preserves future flexibility while keeping the first end-to-end version small enough to ship quickly.
