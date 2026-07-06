# MVP Metrics (Phase 1 Core KPIs)

This document specifies the minimum viable set of metrics selected for initial implementation in Phase 1. These 10 metrics are designed to answer three essential business questions:
1. Is the system running stably?
2. Where in the pipeline funnel are items being lost (leakage)?
3. Are operational workloads/costs being dominated by low-value sources?

## MVP Metrics List

1. **Ingest Volume**
   - **Description**: Total number of source items ingested.
   - **Formula**: Count of records in the `source_item` table within the lookback window.
   - **Data Source**: `source_item`
   - **Direct Dimensions**: `source_item_id`, `source_id`
   - **Derived Dimensions**: None

2. **Fetch Success Rate**
   - **Description**: The percentage of fetch attempts that completed successfully.
   - **Formula**: $$\text{Fetch Success Rate} = \frac{\text{Successful Fetch Attempts (fetch\_attempt.outcome = 'success')}}{\text{Total Fetch Attempts}}$$
   - **Data Source**: `fetch_attempt`
   - **Direct Dimensions**: `source_id`, `fetch_attempt_id`
   - **Derived Dimensions**: None (directly associated with `source_id` configuration)

3. **Low-Context Bypass Rate**
   - **Description**: Proportion of ingested items that bypass classification early due to low context (e.g., title-only snippets).
   - **Formula**: $$\text{Low-Context Bypass Rate} = \frac{\text{Low-Context Ingested Items (source\_item\_text.is\_low\_context = 1)}}{\text{Total Ingested}}$$
   - **Data Source**: `source_item_text`, `source_item`
   - **Direct Dimensions**: `source_item_id`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

4. **Relevance Rate**
   - **Description**: The percentage of classified items that are relevant (core or adjacent).
   - **Formula**: $$\text{Relevance Rate} = \frac{\text{Classify Core} + \text{Classify Adjacent}}{\text{Total Classified (items with a row in classification\_result)}}$$
   - **Data Source**: `classification_result`, `source_item`
   - **Direct Dimensions**: `source_item_id` (from `classification_result`)
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

5. **Curation Approval Rate**
   - **Description**: The percentage of curated items that are approved by editors or the system.
   - **Formula**: $$\text{Curation Approval Rate} = \frac{\text{Curate Approved Count}}{\text{Total Curated Items (items with a row in curation\_decision)}}$$
   - **Data Source**: `curation_decision`
   - **Direct Dimensions**: `source_item_id`, `decision_actor` (natively `curation_decision.decision_actor` is 'system' or 'operator')
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

6. **Overall Yield**
   - **Description**: The total conversion rate from ingestion to approved curation.
   - **Formula**: $$\text{Overall Yield} = \frac{\text{Curate Approved Count}}{\text{Total Ingested (items with a row in source\_item)}}$$
   - **Data Source**: `curation_decision`, `source_item`
   - **Direct Dimensions**: `source_item_id`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

7. **Translation Success Rate**
   - **Description**: The success rate of translation jobs.
   - **Formula**: $$\text{Translation Success Rate} = \frac{\text{Successful Translations (translation\_status = 'completed')}}{\text{Total Translation Attempts}}$$
   - **Data Source**: `translation_output`
   - **Direct Dimensions**: `source_item_id`, `language_code`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

8. **Translation Latency**
   - **Description**: Time elapsed from curation approval to translation completion.
   - **Formula**: $$\text{Average Latency} = \text{Average}(\text{translation\_output.translated\_at} - \text{approved\_content\_record.approved\_at})$$
   - **Data Source**: `translation_output`, `approved_content_record`
   - **Direct Dimensions**: `source_item_id`, `language_code`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

9. **Publish Count**
   - **Description**: Total number of successfully published content items.
   - **Formula**: Count of records in the `publish_record` table.
   - **Data Source**: `publish_record`
   - **Direct Dimensions**: `source_item_id`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

10. **Workload Volume Proxies**
    *   **A. Classification Character Volume Proxy**
        - **Description**: Estimate of processed character volume for classification.
        - **Formula**: `length(source_item.title) + source_item_text.sanitized_text_length` for items where `source_item_text.is_low_context = 0`.
        - **Data Source**: `source_item`, `source_item_text`
        - **Direct Dimensions**: `source_item_id`
        - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
    *   **B. Curation Character Volume Proxy**
        - **Description**: Estimate of input character volume reviewed by the curation stage for items that reached a recorded curation decision.
        - **Formula**: `length(source_item.title) + source_item_text.sanitized_text_length` for items with a row in `curation_decision`.
        - **Data Source**: `curation_decision`, `source_item`, `source_item_text`
        - **Direct Dimensions**: `source_item_id`, `decision_actor`
        - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
        - **Notes**: This is an input-volume proxy only. It does not capture prompt wrapper size, generated output length, token billing, or actual human reading time.
    *   **C. Translation Character Volume Proxy**
        - **Description**: Estimate of processed character volume for translation workload.
        - **Formula**: `length(approved_content_record.display_title) + length(approved_content_record.content_body)`
        - **Data Source**: `approved_content_record`
        - **Direct Dimensions**: `source_item_id`, `content_language_code`
        - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`), `language_code` (via joining matching `translation_output` rows when per-target-language reporting is required)

---

## Metric Promotion Lifecycle

To prevent KPI inflation and maintain a stable dashboard, the following rules govern changes to this list:
1. **No direct additions**: New metrics cannot be directly added to `MVP_METRICS.md`.
2. **Incubation**: New metrics must start in [EXPLORATORY_SIGNALS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/EXPLORATORY_SIGNALS.md) or [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md).
3. **Promotion criteria**: A metric can only be promoted to MVP if it is regularly tracked, decision-useful, and supported by stable canonical schemas.
