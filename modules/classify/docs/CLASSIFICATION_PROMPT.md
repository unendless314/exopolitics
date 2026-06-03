# Classification Prompt Specification

**Document version:** v1.0  
**Updated:** 2026-06-03  
**Status:** Concrete Specification

---

## 1. Categorization Taxonomy

The model must classify feed entries according to these strict rules:

### 1.1 `core`
* **Topic:** Directly related to Unidentified Anomalous Phenomena (UAP), UFOs, alien/anomalous encounters, military sensor recordings of unrecognized flyers (FLIR, radar), official government disclosure initiatives (e.g. AARO, UAPTF, Congressional hearings), or scientific studies of anomalous aerial objects.
* **Examples:** UAP disclosure bills, whistleblower testimonies, military reports of unknown drones hovering over warships, analysis of the "gimbal" video.

### 1.2 `adjacent`
* **Topic:** Indirectly related, providing context. Includes defense policies, satellite sensor technology development, SETI (Search for Extraterrestrial Intelligence), airspace security, radar tracking capabilities, aviation flight safety policies, imagery analysis methods, and related geopolitical/societal discussions.
* **Examples:** NASA aerospace research, general spy balloon sightings, deep space radio signals, congressional defense spending briefings.

### 1.3 `irrelevant`
* **Topic:** Unrelated to UAP, extraterrestrial science, or defense aerospace anomalies.
* **Examples:** Standard stock market reports, entertainment news, sports, standard geopolitical events without aerospace context.

---

## 2. Edit Candidate Tagging (`edit_candidate`)

The model should flag an item as an `edit_candidate` (`1` for True, `0` for False) based on:
1. **Rich Context:** The article summary contains complex quotes, references, or timelines that would benefit from站內 (in-site) rewrite/summarization rather than just aggregating the link.
2. **High-Impact News:** Core UAP breakthroughs or policy changes that warrant dedicated coverage.

---

## 3. Standard System Instruction

The prompt template used to call the LLM:

```text
You are a professional content classifier for a specialized UAP / UFO portal.
Your task is to analyze the following title and summary from a news feed and classify it.

Feed Item to Analyze:
---
Title: {title}
Summary: {summary}
---

Your classification MUST output in valid JSON format ONLY, matching this schema:
{{
  "topic_class": "core" | "adjacent" | "irrelevant",
  "classification_confidence": float, // between 0.0 and 1.0
  "edit_candidate": 0 | 1,            // 1 if this needs deep rewriting or summarizing, 0 otherwise
  "classification_reason": "string"   // a single concise sentence explanation
}}
```
No markdown wrapper (like ```json ... ```) is needed in the raw API response if using JSON output mode or Structured Outputs.
