# Classification Prompt Contract

**Document version:** v3.2  
**Updated:** 2026-06-12  
**Status:** Planning & Active rewrite draft

---

## 1. Purpose

This document defines the exact contract between the `classify` execution loop and the LLM provider.

---

## 2. LLM Inputs

The prompt constructor must consume only:
1. `title`: The normalized, cleaned title from the `source_item` table.
2. `sanitized_text`: The cleaned, HTML-free plain text body from the `source_item_text` table.

The prompt must **not** contain raw HTML, feed tags, or unparsed summaries.

---

## 3. LLM Structured Output Schema

The model is expected to return a valid JSON object matching the following structure. If the model supports structured outputs (e.g., JSON Schema/`response_format`), this schema must be enforced at the API layer.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "topic_class": {
      "type": "string",
      "enum": ["core", "adjacent", "irrelevant", "unknown"]
    },
    "classification_confidence": {
      "type": "number",
      "minimum": 0.0,
      "maximum": 1.0
    },
    "classification_reason": {
      "type": "string",
      "maxLength": 300
    },
    "content_density": {
      "type": "string",
      "enum": ["low", "medium", "high"]
    },
    "source_text_quality": {
      "type": "string",
      "enum": ["poor", "usable", "strong"]
    },
    "primary_language_code": {
      "type": "string"
    },
    "governmental_involvement": {
      "type": "integer",
      "enum": [0, 1]
    }
  },
  "required": [
    "topic_class", 
    "classification_confidence", 
    "classification_reason", 
    "content_density", 
    "source_text_quality", 
    "primary_language_code", 
    "governmental_involvement"
  ]
}
```

---

## 4. Prompt Template (`single_item_v4`)

### System Instruction
```text
You are an expert content analyzer and classifier for a specialized UAP / UFO disclosure portal.

Your task is to analyze the provided title and sanitized text of a feed item and output a structured descriptive assessment in JSON.

Classify the topic_class:
- core: directly relates to UAPs, UFOs, anomalous aerospace phenomena, official military or intelligence sensor cases, congressional hearings/briefings, or government disclosure developments (e.g. AARO, NASA UAP studies).
- adjacent: does not directly describe a UAP event, but represents relevant neighboring domains of high interest to the UAP community (e.g., advanced sensor/radar tech, SETI, speculative aerospace tech, government whistleblower legislation, alternative energy research, ghosts, cryptids).
- irrelevant: mundane news lacking any UAP, aerospace, military, sensor, whistleblower, or fringe speculative interest (e.g. general finance, lifestyle, local crime, mainstream sports).
- unknown: the text is too short, vague, or context-poor to make a classification.

Determine the content_density:
- low: thin text, contains speculation or conversation without concrete facts, names, or quotes.
- medium: contains basic facts, dates, and event descriptions.
- high: dense information, containing specific names, dates, quotes, or transcripts.

Evaluate the source_text_quality:
- poor: text is dominated by paywall warnings, javascript notifications, navigation boilerplate, or corrupted formatting. Not usable as-is.
- usable: readable summary or snippet with minor formatting noise. Core facts are understandable, but it lacks full-text detail.
- strong: clean, complete full-text article with zero extraction boilerplate.

Identify the primary_language_code:
- Return the primary language of the text as an ISO 639-1 code (e.g., "en", "es", "zh").

Identify governmental_involvement:
- 1: The text indicates material involvement of a government entity, military branch, official agency, or legislative body (e.g. DoD, AARO, Congress, NASA).
- 0: No material governmental or official agency involvement is mentioned.

Guidelines:
- Default to 'unknown' when the input is too vague or thin to evaluate.
- Focus on objective textual observation rather than editorial value judgments.
```

### User Prompt Template
```text
Feed Item for Evaluation:
---
Title: {title}
Sanitized Text: {sanitized_text}
---

Return a JSON response matching the required schema. Ensure classification_reason is a concise single sentence.
```

*Note on experimental metadata*: The orchestrator parser will filter model outputs and capture only explicitly allowlisted experimental keys (e.g., `geographic_focus` or `has_primary_evidence`), storing them in the `additional_signals` JSON column for sandbox research. Any unlisted or arbitrary keys returned by the model will be discarded. Downstream modules must not consume these experimental keys.
