# Translate Prompt Contract

**Document version:** v1.2  
**Updated:** 2026-07-24  
**Status:** Locked Contract  

---

## 1. Purpose

This document defines the interface contract between the `translate` module orchestrator and the LLM provider (Gemini/OpenAI) for content translation.

---

## 2. Upstream Input Source

The input payload is retrieved from the canonical `approved_content_record` representing the mother-draft.
- **`display_title`**: The finalized, clean, de-sensationalized mother-draft title.
- **`summary_short`**: The finalized single-paragraph summary.
- **`bullet_1`**: The first structured bullet (factual claim slot), or `NULL` for `publish_link` items.
- **`bullet_2`**: The second structured bullet (evidence level slot), or `NULL` for `publish_link` items.
- **`bullet_3`**: The third structured bullet (objective implication slot), or `NULL` for `publish_link` items.
- **`target_language`**: The target language name and code (e.g., "English (en)", "Japanese (ja)").

*Note: These field names correspond to the logical fields of the approved content record contract. Their exact database column names in `approved_content_record` are subject to the upstream handoff contract DDL. All five content fields are plain text; UI presentation labels are never part of the input payload.*

---

## 3. LLM Structured Output Schema

To prevent parsing errors and enforce structured formats, the translation API call must request JSON format matching this schema:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "translated_title": {
      "type": "string",
      "maxLength": 500,
      "description": "The de-sensationalized title translated into the target language."
    },
    "translated_summary": {
      "type": "string",
      "description": "The single-paragraph summary translated into the target language."
    },
    "translated_bullet_1": {
      "type": ["string", "null"],
      "description": "Translation of Source Bullet 1 (factual claim). Must be null when the source bullet is null, and a non-empty string when the source bullet is non-empty."
    },
    "translated_bullet_2": {
      "type": ["string", "null"],
      "description": "Translation of Source Bullet 2 (evidence level). Must be null when the source bullet is null, and a non-empty string when the source bullet is non-empty."
    },
    "translated_bullet_3": {
      "type": ["string", "null"],
      "description": "Translation of Source Bullet 3 (objective implication). Must be null when the source bullet is null, and a non-empty string when the source bullet is non-empty."
    }
  },
  "required": ["translated_title", "translated_summary", "translated_bullet_1", "translated_bullet_2", "translated_bullet_3"]
}
```

---

## 4. Prompt Template (`translator_v2`)

> `translator_v2` is a new prompt version. The legacy v1 spliced-Markdown shape and the v2 five-field shape must never coexist in the same database; v2 is activated together with the fresh-database rebuild and the new DDL.

### 4.1 System Instruction
```text
You are a professional multilingual translator specialized in scientific, military, and governmental reporting, with specific expertise in the UAP/UFO (Unidentified Anomalous Phenomena) domain.

Your task is to translate the provided structured fields (title, summary, and up to three bullets) into the specified target language.

You MUST strictly adhere to the following translation and formatting policies:

1. Tone and Style:
   - Maintain a highly calm, objective, neutral, and journalistic tone.
   - Do not add exclamation marks, sensational adjectives, or personal comments.
   - Ensure the translated text reads naturally and grammatically correct in the target language while retaining the exact semantic meaning of the source text.

2. Field Handling:
   - Each input slot (Source Title, Source Summary, Source Bullet 1/2/3) is a standalone plain-text field. Translate each provided field fully and independently.
   - Slot names and their parenthesized descriptions are prompt meta-text only. They must never appear in your output values.
   - Do not add any UI labels, list markers, or presentation prefixes (e.g. "Key Claim:") to the translated values. Output content text only.
   - If a source bullet is given as null, the corresponding response value must be null. If a source bullet is non-empty, the corresponding response value must be a non-empty translated string.

3. Forbidden Behaviors:
   - Do not wrap the JSON output in markdown code blocks like ```json ... ```. Output only raw JSON.
   - Do not include any introductory or concluding text (e.g., "Here is your translation:").
   - Do not invent, hallucinate, or expand the content. If a statement is brief, keep it brief in translation.
   - Do not perform partial translations. Every provided field must be fully translated (do not omit any fields). This requirement does not forbid retaining original English names, acronyms, or proper nouns to preserve traceability.

4. Terminology and Glossary:
   - Refer to the terminology policy for UAP/UFO concepts (e.g., translating UAP as "未確認異常現象 (UAP)" in Japanese and keeping it as "UAP" or "Unidentified Anomalous Phenomena" in English).
   - To preserve search traceability, proper nouns such as names of individuals (e.g., congressmen, officials), specific agencies, congressional committees, military bases, and locations should retain their original English spelling (either standalone or in parentheses following a standard translation, e.g., "全領域異常解決オフィス (AARO)").
   - Standardize names of agencies, congressional committees, and officials according to the project glossary / editorially approved terminology.
```

### 4.2 User Prompt Template
```text
Translate the following article fields to target language: {target_language}

---
Source Title:
{display_title}

Source Summary:
{summary_short}

Source Bullet 1 (factual claim):
{bullet_1}

Source Bullet 2 (evidence level):
{bullet_2}

Source Bullet 3 (objective implication):
{bullet_3}
---

Provide your response in raw JSON format matching the schema:
{
  "translated_title": "Translated title text...",
  "translated_summary": "Translated summary text...",
  "translated_bullet_1": "Translated bullet 1 text, or null",
  "translated_bullet_2": "Translated bullet 2 text, or null",
  "translated_bullet_3": "Translated bullet 3 text, or null"
}
```

---

## 5. Quality and Safety Constraints

1. **Title Length Cap**: The `translated_title` must not exceed 500 characters.
2. **Atomic Execution**: The entire five-field set is sent and received within a single API call per article per target language; segmented calls (e.g. summary and bullets in separate requests) are forbidden. Partial translations are strictly forbidden. If the LLM generates a truncated response or an invalid JSON response, the orchestrator must reject the output entirely, treat the attempt as a failure, write a `failed` status, and queue the item for retry. No corrupted or incomplete translation content may be written to the database.
3. **Response Field Validation**:
   Before saving the translated output to the database, the runner must perform the following field validation checks:
   - **Required Keys**: All five response keys (`translated_title`, `translated_summary`, `translated_bullet_1`, `translated_bullet_2`, `translated_bullet_3`) must be present.
   - **Non-Empty Required Strings**: `translated_title` and `translated_summary` must be non-empty strings after trimming leading/trailing whitespace; whitespace-only values are invalid.
   - **Null-In / Null-Out Nullability**: For each bullet slot, if the source bullet is `NULL`, the corresponding translated bullet must be `NULL`; if the source bullet is a non-empty string, the corresponding translated bullet must be a non-empty string after trimming. Whitespace-only bullet values are invalid, and partially populated bullet sets must be rejected.
   - **Validation Failure Outcome**: If any validation check fails, the runner must discard the output, treat it as a failure (status = `'failed'`), increment the `retry_count`, and log the specific validation error.
4. **Self-Translation Bypass**: When the target language equals the mother-draft language (e.g. `en`), the runner copies the five source fields directly without invoking the API, writing `model_name = 'bypass'` and `prompt_version = 'bypass'`. See [EXECUTION_POLICY.md](./EXECUTION_POLICY.md#6-self-translation-bypass-policy) for the full bypass rules.