# Translation Policy and Terminology Glossary

**Document version:** v1.1  
**Updated:** 2026-06-19  
**Status:** Locked Contract  

---

## 1. Purpose

This document establishes the editorially approved translation policies, stylistic rules, formatting constraints, and domain-specific terminology glossaries for the `translate` module. This ensures maximum consistency across all target languages (English, Japanese, etc.) and maintains the journalistic integrity of the portal.

---

## 2. Stylistic and Formatting Guidelines

All translations must strictly follow these rules:

1. **Calm, Objective Journalistic Tone**:
   - The tone must remain detached, clinical, and objective.
   - Never translate sensationalized words literally if they sound overly emotional in the target language. Use neutral equivalents.
   - Do not add exclamation marks or rhetorical questions.
2. **Markdown Structure Preservation**:
   - Headers (`#`, `##`, `###`), list markers (`*`, `-`, `1.`), bolding (`**`), and blockquotes (`>`) must map exactly 1-to-1 to the source document structure.
   - Markdown line breaks and paragraphs must not be merged or split unless necessary for target language grammar.
3. **No Added Commentary or Translator Notes**:
   - The model must never output notes like "*(Translator note: ...)*" or wrap the response in conversational preamble.
   - If the source text contains ambiguous terms, they must be translated neutrally without adding clarifying assumptions.
4. **Proper Nouns and Traceability**:
   - To preserve search traceability, proper nouns such as names of individuals (e.g., congressmen, officials), specific agencies, congressional committees, military bases, and locations should retain their original English spelling (either standalone or in parentheses following a standard translation, e.g., "全領域異常解決オフィス (AARO)").
   - Standard domain acronyms (UAP, UFO, NHI) should be kept in English or included in parentheses on first mention.

---

## 3. Domain Terminology Glossary

To maintain consistency in UAP (Unidentified Anomalous Phenomena) and governmental reporting, the following translations are locked as the project glossary / editorially approved terminology:

| English Term | Traditional Chinese (Source) | Japanese Translation | Notes / Guidelines |
| :--- | :--- | :--- | :--- |
| **UAP / Unidentified Anomalous Phenomena** | UAP / 未確認異常現象 | 未確認異常現象 (UAP) | Japanese translation should include the acronym in parentheses on first mention. |
| **UFO / Unidentified Flying Object** | UFO / 未確認飛行物體 | 未確認飛行物体 (UFO) | Use standard localized kanji. |
| **NHI / Non-Human Intelligence** | NHI / 非人類智能 | 非人間知性 (NHI) | Project glossary terminology. |
| **AARO / All-domain Anomaly Resolution Office** | AARO / 全領域異常解決辦公室 | 全領域異常解決オフィス (AARO) | Project glossary terminology. |
| **Congressional Hearing** | 國會聽證會 | 議会公聴会 | Use standard Japanese parliamentary term. |
| **Declassified Document** | 解密檔案 | 機密解除文書 | Keep distinct from generic files. |
| **Whistleblower** | 內部告發者 / 吹哨者 | 内部告発者 | Journalistic terminology. |
| **Sensor data / Radar logs** | 感測器數據 / 雷達日誌 | センサーデータ / レーダーログ | Technical data references. |

---

## 4. Length and Space Constraints

1. **Display Title Limits**:
   - English: Maximum 500 characters.
   - Japanese: Maximum 120 characters (due to information density in double-byte characters).
2. **Content Length**:
   - The translated body must remain proportional to the mother-draft. Significant expansion (more than 1.2x of raw character length equivalents) is treated as validation failure.

*Note: Any violation of these title length or content length constraints is verified runner-side during validation and will transition the task status to `'failed'` (incrementing `retry_count`) as defined in [EXECUTION_POLICY.md](./EXECUTION_POLICY.md#5-runner-side-content-validation-rules).*

