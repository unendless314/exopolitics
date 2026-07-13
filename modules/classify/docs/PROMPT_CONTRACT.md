# Classification Prompt Contract

**Document version:** v3.3  
**Updated:** 2026-06-13  
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
- adjacent: does not directly describe a UAP event, but represents relevant neighboring domains of high interest to the UAP community (e.g., ancient astronaut theory, SETI, speculative aerospace tech, government whistleblower legislation, alternative energy research, psionics, cryptids).
- irrelevant: mundane news lacking any UAP, aerospace, military, sensor, whistleblower, or fringe speculative interest (e.g. general finance, lifestyle, local crime, mainstream sports).
- unknown: the text is too vague or context-poor to make a classification.

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

Evaluate the content_timeliness:
Time-orientation of the subject matter. MUST NOT infer this field from any publication date or external timestamp.
  - Enum: ["current", "evergreen", "historical", "unclear"]
  - Precedence Rule: If the text contains elements matching multiple time-orientations, select the single type that ranks highest in the following strict hierarchy (1 is highest, 4 is lowest):
      1. current (If a current news report discusses historical cases or scientific preprints, prioritize current as the outer event wrapper)
      2. evergreen (If a scientific study analyzes a historical case, prioritize evergreen to capture its scientific methodology value over pure narrative retrospective)
      3. historical
      4. unclear

Determine the primary_evidence_type:
The primary evidence form discussed. Do not judge if the evidence is authentic; classify what is claimed.
  - Enum: ["physical_material", "radar_sensor", "video_photo", "eyewitness", "official_document", "scientific_paper", "media_report", "none"]
  - Precedence Rule: If multiple evidence types are discussed in the text, you MUST select the single type that ranks highest in the following strict hierarchy (1 is highest, 8 is lowest):
      1. physical_material
      2. radar_sensor
      3. video_photo
      4. official_document (If an official document contains scientific analysis, prioritize official_document to preserve its government source provenance)
      5. scientific_paper
      6. eyewitness
      7. media_report
      8. none

Guidelines:
- Default topic_class to 'unknown' when the input is too vague or ambiguous to evaluate.
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

*Note on experimental metadata*: The orchestrator parser will filter model outputs and capture only explicitly allowlisted experimental keys (e.g., `content_timeliness` or `primary_evidence_type`), storing them in the `additional_signals` JSON column for sandbox research. Any unlisted or arbitrary keys returned by the model will be discarded. Downstream modules must not consume these experimental keys.

---

## 5. Optional Experimental Schema (Sandbox)

To support downstream triage research, the orchestrator allowlists specific experimental keys. These keys must **not** be added to the stable schema `required` array, but if returned by the model, they will be captured and saved in `additional_signals`.

> [!NOTE]
> **OpenAI Strict Mode Compatibility Notice**:
> Under OpenAI Structured Outputs with `strict: True` enabled, all defined schema properties must technically be placed in the schema's `required` array. To support this API requirement while keeping the fields semantically optional, they are defined as `required-but-nullable` (allowing `null` or `None` values) in the JSON schema sent to the model. However, in the Python-side orchestrator validator, they remain strictly optional and their absence is accepted.

### Allowed Experimental Key: `content_timeliness`
*   **Type**: `string`
*   **Enum**: `["current", "evergreen", "historical", "unclear"]`
*   **Temporal Inference Rule**: The model MUST NOT infer this field from any publication date or external timestamp (which are not provided in the input). It must evaluate only the semantic time-orientation of the discussed subject matter (e.g., Roswell = `historical`, a new hearing = `current`, theoretical research = `evergreen`).
*   **Precedence Rule**: If the text contains elements matching multiple time-orientations, select the single type that ranks highest in the following strict hierarchy (1 is highest, 4 is lowest):
    1. `current` (If a current news report discusses historical cases or scientific preprints, prioritize `current` as the outer event wrapper)
    2. `evergreen` (If a scientific study analyzes a historical case, prioritize `evergreen` to capture its scientific methodology value over pure narrative retrospective)
    3. `historical`
    4. `unclear`

### Allowed Experimental Key: `primary_evidence_type`
*   **Type**: `string`
*   **Enum**: `["physical_material", "radar_sensor", "video_photo", "eyewitness", "official_document", "scientific_paper", "media_report", "none"]`
*   **Authenticity Rule**: This field MUST strictly describe the type of evidence claimed or discussed in the text. The model MUST NOT attempt to judge or verify if the evidence is authentic, reliable, or true. It is a descriptive categorizer of the text's subject matter (e.g., pilot eyewitness account = `eyewitness`, declassified radar tracking = `radar_sensor`, recovered metal wreckage = `physical_material`).
*   **Precedence Rule**: If multiple evidence types are discussed in the text, you MUST select the single type that ranks highest in the following strict hierarchy (1 is highest, 8 is lowest):
    1. `physical_material`
    2. `radar_sensor`
    3. `video_photo`
    4. `official_document` (If an official document contains scientific analysis, prioritize `official_document` to preserve its government source provenance)
    5. `scientific_paper`
    6. `eyewitness`
    7. `media_report`
    8. `none`

### Future Candidate Keys
The following keys are intentionally not allowlisted in the current phase. They are documented here as future research candidates only and must not be requested or persisted by the current classify runs.

*   `subject_nature`
    *   **Type**: `string`
    *   **Enum**: `["encounter_case", "legislative_policy", "scientific_analysis", "disclosure_advocacy"]`
    *   **Role**: Serves as a sub-topic classifier for relevant items (where `topic_class` is the primary relevance gate).

*   `sensationalism_level`
    *   **Type**: `string`
    *   **Enum**: `["low", "medium", "high"]`
    *   **Stylistic Focus Rule**: This field MUST measure the level of stylistic exaggeration and clickbait features (capitalization, exclamation points, hyperbole). It MUST NOT evaluate the factuality or scale of the physical anomaly reported.

