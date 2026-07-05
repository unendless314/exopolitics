# Exploratory Signals

This document defines experimental metrics and signals parsed from the database that are NOT to be used as stable operational KPIs or dashboard dependencies. They serve as sandbox signals for testing future utility.

## 1. Experimental Signals Grounding
The `analysis` module is permitted to read and parse allowlisted keys stored inside the JSON column `classification_result.additional_signals`. 

### Current Allowlisted Keys
1. **`content_timeliness`**: Represents how time-sensitive the incoming content is.
2. **`primary_evidence_type`**: Represents the type of evidence backing the UAP/UFO claim (e.g., `official_document`, `media_report`, `eyewitness_account`).

---

## 2. Governance & Sandbox Rules
To prevent system instability, the following governance rules are enforced:
*   **Observational Use Only**: Exploratory metrics may appear in reports as observational distributions, correlation checks, or slices, but they must be explicitly labeled as **EXPERIMENTAL**.
*   **No Operational Dependencies**: No operational module (e.g., `curate`, `translate`, `publish`) may use these experimental fields for routing decisions, publication eligibility, or canonical state transitions.
*   **Promotion Pathway**: Before any exploratory signal can be promoted to a stable KPI in the [Metrics Catalog](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md):
    1. It must demonstrate consistent stability across models and prompts.
    2. It must be non-sparse (populated for a significant majority of inputs).
    3. It must prove actionable for decisions (e.g. showing a clear correlation to high curation rates).

---

## 3. Recommended MVP Exploratory Questions
These questions should guide early analysis reporting on these signals:
*   Does `content_timeliness` correlate with curation approval outcomes or source usefulness?
*   Does `primary_evidence_type` reveal meaningful source specialization (e.g., is a feed dominated by government reports vs media gossip)?
*   Are these signals stable enough across prompts and model changes to justify future schema promotion?
