# Classification Policy

**Document version:** v2.0  
**Updated:** 2026-06-03  
**Status:** Active

---

## 1. Purpose

This document defines what each `topic_class` means and how low-context items are handled in the MVP.

---

## 2. Topic Classes

### 2.1 `core`

Directly related to UAP, UFOs, anomalous encounters, government disclosure, official investigations, military sensor cases, or scientific discussion of anomalous aerial objects.

Examples:

* UAP hearing coverage
* AARO or UAPTF announcements
* military reports on unknown aerial activity
* analysis of well-known UAP footage

### 2.2 `adjacent`

Related context that may matter to UAP coverage but is not itself clearly central to the topic.

Examples:

* defense policy
* radar and sensing technology
* SETI-related developments
* aviation safety or airspace security
* imagery analysis methods

### 2.3 `irrelevant`

No meaningful relation to UAP, extraterrestrial science, or aerospace anomaly coverage.

Examples:

* general finance news
* entertainment coverage
* sports
* ordinary politics with no aerospace or anomaly angle

### 2.4 `unknown`

The available feed metadata is not sufficient for a reliable classification.

`unknown` is:

* a valid classification result
* not a failure state
* not the same as `irrelevant`
* a useful candidate pool for future enrichment or manual triage

---

## 3. Two Paths To `unknown`

### 3.1 Deterministic pre-check path

If the combined `title + summary` length is below `min_context_characters`, the module may assign `unknown` without calling the LLM.

This path exists to:

* save API cost
* avoid forcing unreliable guesses from very short input
* keep behavior predictable for obviously thin feed entries

### 3.2 LLM judgment path

If an item passes the minimum context threshold, it may still be classified by the model as `unknown` when the available text is long enough but still too ambiguous, vague, contradictory, or context-poor for a reliable decision.

Examples:

* a long teaser that never states what the event actually is
* sensational wording with little factual content
* text that references prior context not present in the feed item

---

## 4. `edit_candidate` Policy

`edit_candidate = 1` is appropriate when the item appears strong enough to justify later rewrite, summarization, or dedicated editorial handling.

Typical signals:

* high-impact `core` developments
* dense summaries with timelines, quotations, or many factual details
* items that are likely to benefit from editorial context rather than simple aggregation

The following rules apply in the MVP:

* deterministic `unknown` items must set `edit_candidate = 0`
* `irrelevant` items should normally set `edit_candidate = 0`
* `adjacent` and LLM-produced `unknown` items may still set `edit_candidate = 1` if the model has a clear reason

---

## 5. Out Of Scope

This document does not define:

* review queue policy
* enrichment queue ownership
* full-text retrieval rules
* future workflow-state tables

Those belong in later module or shared-capability contracts.
