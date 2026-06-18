# Translate Prompt Contract

**Document version:** v1.1  
**Updated:** 2026-06-19  
**Status:** Locked Contract  

---

## 1. Purpose

This document defines the interface contract between the `translate` module orchestrator and the LLM provider (Gemini/OpenAI) for content translation.

---

## 2. Upstream Input Source

The input payload is retrieved from the canonical `approved_content_record` representing the mother-draft.
- **`display_title`**: The finalized, clean, de-sensationalized mother-draft title.
- **`content_body`**: The finalized Markdown body, spliced from curation outputs or edited by human operators.
- **`target_language`**: The target language name and code (e.g., "English (en)", "Japanese (ja)").

*Note: These field names correspond to the logical fields of the approved content record contract. Their exact database column names in `approved_content_record` are subject to the upstream handoff contract DDL.*

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
      "maxLength": 250,
      "description": "The de-sensationalized title translated into the target language."
    },
    "translated_content": {
      "type": "string",
      "description": "The complete markdown content body translated into the target language, preserving all markdown syntax."
    }
  },
  "required": ["translated_title", "translated_content"]
}
```

---

## 4. Prompt Template (`translator_v1`)

### 4.1 System Instruction
```text
You are a professional multilingual translator specialized in scientific, military, and governmental reporting, with specific expertise in the UAP/UFO (Unidentified Anomalous Phenomena) domain.

Your task is to translate the provided title and markdown content body into the specified target language.

You MUST strictly adhere to the following translation and formatting policies:

1. Tone and Style:
   - Maintain a highly calm, objective, neutral, and journalistic tone.
   - Do not add exclamation marks, sensational adjectives, or personal comments.
   - Ensure the translated text reads naturally and grammatically correct in the target language while retaining the exact semantic meaning of the source text.

2. Markdown Preservation:
   - You MUST preserve all markdown formatting exactly as provided in the source text.
   - Do not alter or omit headers (#, ##, ###), list markers (*, -, numbered lists), bold text (**), italic text (*), inline code, links, or code blocks.
   - Ensure blockquotes and list structures remain syntactically identical in translation.

3. Forbidden Behaviors:
   - Do not wrap the JSON output in markdown code blocks like ```json ... ```. Output only raw JSON.
   - Do not include any introductory or concluding text (e.g., "Here is your translation:").
   - Do not invent, hallucinate, or expand the content. If a statement is brief, keep it brief in translation.
   - Do not perform partial translations. The entire text must be fully translated.

4. Terminology and Glossary:
   - Refer to the terminology policy for UAP/UFO concepts (e.g., translating UAP as "未確認異常現象 (UAP)" in Japanese and keeping it as "UAP" or "Unidentified Anomalous Phenomena" in English).
   - Standardize names of agencies, congressional committees, and officials according to the project glossary / editorially approved terminology.
```

### 4.2 User Prompt Template
```text
Translate the following article to target language: {target_language}

---
Source Title:
{display_title}

Source Content Body:
{content_body}
---

Provide your response in raw JSON format matching the schema:
{
  "translated_title": "Translated title text...",
  "translated_content": "Translated markdown body..."
}
```

---

## 5. Quality and Safety Constraints

1. **Title Length Cap**: The `translated_title` must not exceed 250 characters.
2. **Atomic Execution**: Partial translations are strictly forbidden. If the LLM generates a truncated body or an invalid JSON response, the orchestrator must reject the output entirely, treat the attempt as a failure, write a `failed` status, and queue the item for retry. No corrupted or incomplete translation content may be written to the database.
3. **Markdown Structural Validation**:
   Before saving the translated output to the database, the runner must perform the following structural validation checks:
   - **Code Fence Symmetry**: The count of code fences (triple backticks ` ``` `) in `translated_content` must be even and match the number of code blocks in `content_body`.
   - **Link Syntax Preservation**: Validate that markdown links `[link text](url)` are not malformed (e.g. mismatched brackets or parentheses).
   - **Header Preservation**: The count and level of markdown headers (e.g. `#`, `##`) in the translated output must match the structure of the input source.
   - **Validation Failure Outcome**: If any validation check fails, the runner must discard the output, treat it as a failure (status = `'failed'`), increment the `retry_count`, and log the specific validation error.

