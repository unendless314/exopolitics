# Monitoring Module Revision Plan (Resolved Strategy)

This document establishes a unified, reconciled plan for the `analysis` module. It combines the database-alignment corrections from the initial proposal with the critical operational queue insights from the peer review.

---

## 1. Metric Action Plan

| Metric / Feature | Proposal | Peer Review | **Reconciled Decision** |
| :--- | :--- | :--- | :--- |
| **`publish_record.published_at`** | Rename to `first_published_at` | Agree (Must adopt) | **Rename to `first_published_at`** (aligns with schema). |
| **`published_by_language`** | Add breakdown with coverage rate | Agree, but refine denominator | **Add breakdown**; coverage rate denominator is corrected to match the numerator's time-window scope (see Section 2.1). |
| **Translation Delay Bypass Filter** | Exclude `model_name = 'bypass'` | Agree (Must adopt) | **Exclude bypass rows** to prevent 0-second latency pollution. |
| **Consolidate Latency Definitions** | Merge 4.2.5 and 4.3.5 | Agree (Must adopt) | **Merge into single catalog entry** with multiple view scopes. |
| **Translation Workload Proxy** | Use curation-based upper bound | Retain DB-recorded, add upper bound | **Implement Dual Metrics**: `recorded_workload` (minus bypass) and `intended_workload_upper_bound`. |
| **Topic Class Breakdown** | Add to CLI and JSON schema | Agree, but keep under `analyze-sources` | **Implement** as `analyze-sources` breakdown; keep `funnel` clean. |
| **CLI Default Configuration** | Rename `decision_rules.yaml` | Add new file instead of renaming | **Rename `decision_rules.yaml` to `analysis_settings.yaml`** to align with module naming patterns (e.g. `publish_settings.yaml`). Incorporates a systematic code/doc reference update checklist (see Section 3). |
| **Classification Filtering Overhead** | Delete entirely | Downgrade to catalog/exploratory | **Downgrade to Catalog**: Remove from MVP dashboard; keep in catalog/exploratory. |
| **Translation Completion Rate** | Delete entirely | Keep (backlog exists due to batch size) | **Restore Article-Level Definition**: Define as the percentage of approved articles that completed translation across all required target languages (fixing the self-translation bypass grain mismatch) (see Section 2.3). |
| **Translation Stale Rate** | Delete entirely | Keep in catalog as diagnostics | **Downgrade to Catalog**: Remove from MVP dashboard; keep in catalog/diagnostics. |
| **Unique Contribution Rate** | Delete if validation is low | Defer to exploratory / Phase 2 | **Reposition to Phase 2**: Remove from MVP; evaluate cross-source duplicates in Phase 2. |
| **`DECISION_MODELS.md`** | Defer to Phase 2 | Agree (Must adopt) | **Defer implementation to Phase 2**: Preserve the document as a policy draft, but treat `[AUTHORITY]` as tagging only, not an automated recommendation override. |

---

## 2. Technical Refinements & SQL Specifications

### 2.1 Multi-Language Coverage Rate Time-Window Alignment
To prevent time-window mismatch where the numerator is window-filtered but the denominator spans all-time, we define two aligned options:

#### Option A: Cohort-Based Funnel Coverage (Recommended for Funnel Report)
Tracks the language coverage rate for the specific cohort of items fetched during the lookback window. Both numerator and denominator are bound to the same ingestion cohort.
* **SQL Query**:
  ```sql
  SELECT 
      pls.language_code,
      COUNT(DISTINCT pr.publish_record_id) AS published_count,
      ROUND(COUNT(DISTINCT pr.publish_record_id) * 1.0 / NULLIF((
          SELECT COUNT(DISTINCT pr2.publish_record_id)
          FROM publish_record pr2
          JOIN source_item si2 ON pr2.source_item_id = si2.source_item_id
          WHERE si2.fetched_at BETWEEN :start AND :end
      ), 0), 4) AS coverage_rate
  FROM publish_language_status pls
  JOIN publish_record pr ON pls.publish_record_id = pr.publish_record_id
  JOIN source_item si ON pr.source_item_id = si.source_item_id
  WHERE pls.publish_status = 'published'
    AND si.fetched_at BETWEEN :start AND :end
  GROUP BY pls.language_code;
  ```

#### Option B: All-Time Snapshot Coverage (Alternative)
Tracks the absolute global translation coverage across the entire database history, omitting the lookback window constraint from both numerator and denominator.
* **SQL Query**:
  ```sql
  SELECT 
      pls.language_code,
      COUNT(*) AS published_count,
      ROUND(COUNT(*) * 1.0 / (SELECT COUNT(*) FROM publish_record), 4) AS coverage_rate
  FROM publish_language_status pls
  WHERE pls.publish_status = 'published'
  GROUP BY pls.language_code;
  ```

---

### 2.2 Dual Translation Workload Proxies (Excluding Bypasses)
To solve the crash-risk underestimation while keeping actual execution history:
1. **`recorded_translation_workload`**: Characters actively processed by LLM. Must filter out `model_name = 'bypass'`.
   ```sql
   SELECT SUM(length(acr.display_title) + length(acr.content_body))
   FROM approved_content_record acr
   JOIN translation_output tor ON acr.parent_content_id = tor.parent_content_id
   WHERE tor.translated_at BETWEEN :start AND :end
     AND tor.model_name != 'bypass';
   ```
2. **`intended_translation_workload_upper_bound`**: Total characters approved for translation (queue size multiplied by target languages excluding bypass).
   * Calculated by scanning `approved_content_record` and checking each item's `content_language_code`. If target languages are `{en, ja, zh}`, and an item's source language is `en`, it needs `2` translations. If its source is `zh`, it needs `2` translations.
   * **Formula**:
     $$\sum_{\text{items}} \text{length}(item) \times (\text{Total Target Languages} - 1)$$

---

### 2.3 Translation Completion Rate Granularity Corrections
To prevent grain mismatch (where self-translation bypasses instantly skew the numerator under simple counts), the completion metrics are defined at two strict levels:

1. **Global Article-Level Completion Rate**:
   * **Formula**:
     $$\text{Translation Completion Rate} = \frac{\text{Approved Articles with All Target Translations Completed}}{\text{Total Approved Articles}}$$
   * **Definition**: An article is considered completed only if all required non-bypass target language outputs for that article have a status of `'completed'`. The required count must be derived per article from the active target language set and the article's source language.
2. **Per-Locale Completion Rate (for language $L$)**:
   * **Formula**:
     $$\text{Locale Completion Rate}_L = \frac{\text{Completed Translations for Language } L}{\text{Total Approved Items (where source language } \ne L\text{)}}$$

---

## 3. Configuration Naming & References Update Checklist

To adopt `analysis_settings.yaml` systematically without introducing inconsistent contracts, the renaming task must include a synchronized update of all file references:

### 3.1 Systematic Reference Updates
* Rename the file [decision_rules.yaml](file:///C:/Users/user/Documents/exopolitics/modules/analysis/config/decision_rules.yaml) to `analysis_settings.yaml` under `modules/analysis/config/`.
* Update [DECISION_MODELS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DECISION_MODELS.md#L57) and [DECISION_MODELS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DECISION_MODELS.md#L60) to point to `analysis_settings.yaml`.
* Update [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md#L25-L26) override defaults references to read from `analysis_settings.yaml`.
* Update all Python parser code in `modules/analysis/src/` loading the config directory to bind to `analysis_settings.yaml` instead of `decision_rules.yaml`.

### 3.2 Proposed Configuration Schema (`modules/analysis/config/analysis_settings.yaml`)

```yaml
schema_version: 1

# Reporting CLI Defaults (Phase 1)
reporting:
  defaults:
    days: 7
    format: "markdown"
    output_dir: "reports/analysis/"
    stdout: false

# Quadrant Source Classifier settings (Deferred to Phase 2)
quadrant_classifier:
  thresholds:
    overall_yield: 0.10          # High Yield >= 10%
    relevance_rate: 0.40         # High Relevance >= 40%
  safeguards:
    fetch_success_rate_isolation: 0.50  # Fetch Isolation under 50%
```

---

## 4. Phase 1 Implementation Scope

We will execute the revisions in a phased manner to safeguard the Phase 1 release timeline:

### Phase 1 (Must Complete for Release)
* Correct `published_at` -> `first_published_at` across all documents and code queries.
* Update `translation_delay` SQL and catalog definition to exclude `'bypass'` runs.
* Merge the duplicate translation latency metrics.
* Update [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) and the JSON schema to include `published_by_language` breakdown and `topic_class_breakdown` (under `analyze-sources`).
* Rename `decision_rules.yaml` to `analysis_settings.yaml` and update all referenced documentation and python code.

### Phase 2 (Deferred to Post-Release Optimization)
* Automated recommendation rules and heuristics in [DECISION_MODELS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DECISION_MODELS.md).
* Source unique contribution rate (pending database-backed duplication studies).
* Dashboard UI visualizations.
