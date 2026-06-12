# Classification Policy

**Document version:** v3.2  
**Updated:** 2026-06-12  
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
* *Examples:* SETI research, advanced radar sensor capabilities, aviation safety reforms, deep space anomalies, psionics, cryptids, alternative energy tech, general government whistleblower legislation.

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
*   An optional JSON field for experimental metadata (e.g., `geographic_focus` or `has_primary_evidence`).
*   **Guardrail**: Downstream consumer modules must not query or depend on any key within `additional_signals`. Any signal promoted to stable usage must be migrated to a first-class, typed database column.
