# Classification Policy

**Document version:** v3.3  
**Updated:** 2026-06-13  
**Status:** Planning & Active rewrite draft

---

## 1. Purpose

This policy governs how items are categorized and how decisions are routed between deterministic rules and LLM evaluation.

---

## 2. Topic Classes

### 2.1 `core`
Directly related to UAP, UFOs, anomalous encounters, government disclosure, official investigations (e.g., AARO, NASA UAP study), military sensor encounters, or scientific discussion of anomalous aerospace objects.
* *Examples:* Congressional UAP hearings, military pilot hazard reports of unknown objects, declassified sensor footage, scientific papers analyzing anomalous acceleration.

### 2.2 `adjacent`
Context that does not directly describe a UAP event, but represents neighboring spaces relevant to the community (speculative science, advanced defense tech, whistleblowers, fringe topics).
* *Examples:* SETI research, ancient astronaut theory, aviation safety reforms, deep space anomalies, psionics, cryptids, alternative energy tech, general government whistleblower legislation.

### 2.3 `irrelevant`
Mundane news lacking any anomalous, aerospace, speculative, or fringe interest.
* *Examples:* General finance markets, mainstream sports, routine political bills, celebrity gossip, local crime reports, standard software updates.

### 2.4 `unknown`
Assigned when the item is too vague, context-poor, or ambiguous to classify. This category is a normal, valid destination, not an error.
* *Deterministic Path:* Triggered automatically when an item is flagged by the sanitizer as low-context.
* *LLM Path:* Triggered when the text is long enough but fails to describe any clear subject or relies heavily on absent external context.

---

## 3. Low-Context Routing Policy

`classify` relies on the sanitization flags populated during `ingest` to optimize API usage and avoid hallucinations.

### Processing Rule
For each pending item:
1. **If `source_item_text.is_low_context == 1`:**
   * Bypass the LLM call entirely.
   * Write the classification result with the following deterministic attributes:
     * `topic_class = 'unknown'`
     * `classification_reason = 'Deterministic bypass: Item flagged as low-context during ingestion.'`
     * `classification_confidence = NULL`
     * `content_density = NULL`
     * `source_text_quality = NULL`
     * `primary_language_code = NULL`
     * `governmental_involvement = NULL`
     * `additional_signals = NULL`
     * `model_name = 'deterministic-low-context'`
     * `prompt_version = 'rule_v1'`
2. **If `source_item_text.is_low_context == 0`:**
   * Proceed to LLM-based classification.

### Rationale on Classification Reason
We keep `classification_reason` as a clean, uniform human-readable string. We do not concatenate machine-trace code keys like `t.low_context_reason` here, as doing so would pollute the audit field semantics for human reviewers. The original machine-level reason code is already stored in the joined `source_item_text.low_context_reason` column for tracing.

---

## 4. Descriptive Tagging Policy

Instead of deciding prescriptive editorial workflows, `classify` generates structured descriptive signals to assist downstream triage.

### 4.1 Content Density (`content_density`)
*   `low`: The text is thin, conversational, or speculative without concrete facts.
*   `medium`: The text contains basic facts, dates, and event descriptions.
*   `high`: The text is detailed, containing specific quotes, names, transcripts, or multiple source citations.

### 4.2 Source Text Quality (`source_text_quality`)
*   `poor`: The text is dominated by paywall warnings, navigation menu boilerplate, or parsing errors. It is not usable as-is.
*   `usable`: The text represents a readable summary or snippet with minor formatting noise. Key details are present, but it lacks full-text completeness.
*   `strong`: The text is pristine, containing complete full-text articles without extraction boilerplate.

### 4.3 Primary Language (`primary_language_code`)
*   The primary detected language of the text represented as an ISO-style code (e.g., `en` for English, `es` for Spanish).
*   *Note:* The decision of whether translation is needed (`translation_needed`) is a policy decision derived downstream based on the system's working language config.

### 4.4 Official Involvement (`governmental_involvement`)
*   `1`: The text indicates direct involvement of government entities, congressional committees, military branches, or official agencies (e.g. AARO, NASA, DoD).
*   `0`: The text does not mention material governmental or official agency involvement.

### 4.5 Sandbox Metadata (`additional_signals`)
*   An optional JSON field for experimental metadata (e.g., `content_timeliness` or `primary_evidence_type`).
*   **Active Experimental Keys**:
    *   `content_timeliness`: Describes the time-orientation of the subject matter discussed in the content, not the recency of the article's publication timestamp.
        *   **Precedence Rule**: If the text contains elements matching multiple time-orientations, select the single type that ranks highest in the following strict hierarchy (1 is highest, 4 is lowest):
            1. `current`: The content discusses a recent, ongoing, or upcoming current event (e.g., a congressional hearing held yesterday, a planned press briefing next week). If a current news report discusses historical cases or scientific preprints, prioritize `current` as the outer event wrapper.
            2. `evergreen`: The content is a timeless research paper, deep analysis, or theoretical/scientific/data-driven discussion that is not tied to a specific transient event. If a scientific study analyzes a historical case (e.g., chemical analysis of Roswell crash debris), prioritize `evergreen` to capture its scientific methodology value over pure narrative retrospective.
            3. `historical`: The content discusses a historical case, past event, or archival record (e.g., a blog post analyzing the 1947 Roswell incident or the 2004 Nimitz encounter).
            4. `unclear`: The subject matter's time-orientation cannot be clearly determined from the text.
    *   `primary_evidence_type`: Describes the primary evidence form presented or discussed in the content. This is a descriptive metadata key indicating the claimed/discussed evidence type, and does not imply or judge whether the evidence is authentic or factually accurate.
        *   **Precedence Rule**: If multiple evidence types are discussed in the text, you MUST select the single type that ranks highest in the following strict hierarchy (1 is highest, 8 is lowest):
            1. `physical_material`: The text discusses recovered physical wreckage, debris/meta-material samples, physical landing site traces (e.g., ground indentations, radiation traces, soil anomalies), or biological specimens.
            2. `radar_sensor`: The text discusses military/official radar, sonar, or other physical sensor detection data.
            3. `video_photo`: The text discusses visual captures (e.g., photographs, declassified FLIR/sensor video recordings, cockpit footage).
            4. `official_document`: The text discusses or extracts official agency records, congressional testimony transcripts, declassified reports, or government briefings. (If an official document contains scientific analysis, prioritize `official_document` to preserve its government source provenance).
            5. `scientific_paper`: The text is or discusses a theoretical physics study, peer-reviewed scientific paper, or data analysis report.
            6. `eyewitness`: The text relies on direct observer reports (e.g., commercial or military pilot encounter descriptions, civilian eyewitness testimony).
            7. `media_report`: The text is a general news summary or media compilation without a specific primary source record focus.
            8. `none`: No specific primary evidence form or official source record is presented or discussed (e.g., speculative opinion piece, blog discussion).
    *   *Examples & Boundary Cases*:
        *   *Example 1 (Historical)*: A blog article published today detailing a declassified document from 1960. It is classified as `historical` because the subject matter discussed is historical, regardless of the publication timestamp.
        *   *Example 2 (Current)*: A news report about a congressional representative discussing a past event (e.g., the 2004 Nimitz case) in a hearing held yesterday. It is classified as `current` because the primary event being reported is the active political/congressional hearing, not just a historical retrospective.
        *   *Example 3 (Evergreen)*: A physics preprint analyzing the theoretical mechanics of warp drives or anomalous acceleration. It is classified as `evergreen`.
        *   *Example 4 (Evidence Type - Precedence)*: A news story reports on a military pilot who described a visual encounter and confirmed the object was tracked on radar. Because radar/sensor data is mentioned, the primary evidence type is classified as `radar_sensor` (prioritizing hard instrument/sensor data over eyewitness testimony or news summary).
*   **Future Candidate Keys (Not currently allowlisted)**:
    *   `subject_nature`: Intended to describe the within-topic content subtype once an item is classified as relevant (relevance gate: `topic_class`).
        *   `encounter_case`: The text primarily describes military, civilian, or historical UFO/UAP sightings, sensor encounters, or cockpit observations.
        *   `legislative_policy`: The text primarily discusses government legislation (e.g., NDAA amendments), congressional briefings/hearings, or whistleblower protection reforms.
        *   `scientific_analysis`: The text focuses on scientific studies, physics modeling, radar propagation theory, or SETI aerospace research.
        *   `disclosure_advocacy`: The text focuses on civilian advocacy campaigns, public whistleblower declarations, activist press conferences, or general public discourse.
    *   `sensationalism_level`: Intended to measure stylistic exaggeration and clickbait nature of the text, NOT the severity or physical scale of the described event.
        *   `low`: The style is objective, sober, factual, and analytical (e.g., standard scientific preprints or formal military incident reports).
        *   `medium`: The style features moderate speculation, dramatic phrasing, or mild clickbait headlines.
        *   `high`: The style is highly sensationalist, featuring excessive capitalization (ALL CAPS), multiple exclamation marks, or conspiracy-style alarmist phrasing.
*   **Guardrail**: Downstream consumer modules must not query or depend on any key within `additional_signals`. Any signal promoted to stable usage must be migrated to a first-class, typed database column.
