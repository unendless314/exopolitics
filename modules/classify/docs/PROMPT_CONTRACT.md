# Prompt Contract

**Document version:** v2.0  
**Updated:** 2026-06-03  
**Status:** Active

---

## 1. Purpose

This document defines the LLM-facing contract used by `classify`.

It does not define batching, retries, or database schema.

---

## 2. Input Boundary

The MVP classifier uses feed metadata only:

* `title`
* `summary`

Optional contextual fields such as `published_at` or `canonical_url` may be included in internal logging or persistence, but they are not required prompt inputs in the MVP.

---

## 3. When The LLM Is Not Called

The module may skip prompt execution when deterministic pre-checks already show that the feed item is too short to classify reliably.

The current MVP rule is:

* if combined `title + summary` length is below `min_context_characters`, the module may directly assign `topic_class = 'unknown'`

---

## 4. Prompt Requirements

The prompt must instruct the model to:

* classify the item into `core`, `adjacent`, `irrelevant`, or `unknown`
* return valid JSON only
* provide a concise reason
* provide a confidence score between `0.0` and `1.0`
* return `edit_candidate` as `0` or `1`

---

## 5. Example Prompt Template

```text
You are a professional content classifier for a specialized UAP / UFO portal.
Your task is to analyze the following feed item and classify it.

Feed Item:
---
Title: {title}
Summary: {summary}
---

Return valid JSON only, matching this schema:
{
  "topic_class": "core" | "adjacent" | "irrelevant" | "unknown",
  "classification_confidence": float,
  "edit_candidate": 0 | 1,
  "classification_reason": "string"
}
```

---

## 6. Output Semantics

### 6.1 `unknown` from the LLM

The model may return `unknown` when the feed text is long enough to inspect but still does not support a reliable topic judgment.

### 6.2 Confidence

* Confidence is expected for LLM-produced outputs.
* Deterministic pre-check `unknown` results may store `NULL` confidence because no model judgment was made.

### 6.3 Structured output mode

If the selected provider supports native JSON schema or structured output mode, that path should be preferred.

If not, the implementation may fall back to strict JSON parsing of raw text output, but the semantic output contract remains the same.
