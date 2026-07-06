# MVP Metrics (Phase 1 Core KPIs)

This document specifies the minimum viable set of metrics selected for initial implementation in Phase 1. These 10 metrics are designed to answer three essential business questions:
1. Is the system running stably?
2. Where in the pipeline funnel are items being lost (leakage)?
3. Are operational workloads/costs being dominated by low-value sources?

## Time Window Semantics

All MVP metrics are evaluated within a lookback window (default: 7 days, controlled by the CLI `--days` option) to avoid historical dilution and support decision-making. Based on the operational or analytical nature of each metric, they are categorized into two time window semantics:

1. **Cohort Window (`source_item_cohort`)**
   - **Definition**: The lookback window filters the base ingestion records (`source_item.created_at` within the window). Downstream conversion, curation, and workload metrics are calculated strictly for this cohort of items, regardless of when their downstream events (e.g., classification, curation decision) occurred.
   - **Purpose**: Guarantees statistical alignment and mathematical consistency in funnel conversion and yield rates.
   - **Metrics**: Ingest Volume, Low-Context Bypass Rate, Relevance Rate, Curation Approval Rate, Overall Yield, Workload Volume Proxies.

2. **Event-Time Window (`event_time`)**
   - **Definition**: The lookback window filters events using the timestamp of the metric's own primary event table (e.g., `fetch_attempt.created_at`, `translation_output.updated_at`, or `publish_record.published_at`).
   - **Purpose**: Provides real-time operational health, system throughput, and performance monitoring independent of ingestion cohort timing.
   - **Metrics**: Fetch Success Rate, Translation Success Rate, Translation Latency, Publish Count.

---

## MVP Metrics List

1. **Ingest Volume**
   - **Window Semantic**: `source_item_cohort`
   - **Description**: Total number of source items ingested.
   - **Formula**: Count of records in the `source_item` table where `source_item.created_at` is within the lookback window.
   - **Data Source**: `source_item`
   - **Direct Dimensions**: `source_item_id`, `source_id`
   - **Derived Dimensions**: None

2. **Fetch Success Rate**
   - **Window Semantic**: `event_time`
   - **Description**: The percentage of fetch attempts that completed successfully.
   - **Formula**: $$\text{Fetch Success Rate} = \frac{\text{Successful Fetch Attempts (fetch\_attempt.outcome = 'success')}}{\text{Total Fetch Attempts}}$$ where `fetch_attempt.created_at` is within the lookback window.
   - **Data Source**: `fetch_attempt`
   - **Direct Dimensions**: `source_id`, `fetch_attempt_id`
   - **Derived Dimensions**: None

3. **Low-Context Bypass Rate**
   - **Window Semantic**: `source_item_cohort`
   - **Description**: Proportion of items in the ingested cohort that bypass classification early due to low context.
   - **Formula**: $$\text{Low-Context Bypass Rate} = \frac{\text{Low-Context Ingested Items (source\_item\_text.is\_low\_context = 1)}}{\text{Total Ingested Items}}$$ where `source_item.created_at` is within the lookback window.
   - **Data Source**: `source_item_text`, `source_item`
   - **Direct Dimensions**: `source_item_id`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

4. **Relevance Rate**
   - **Window Semantic**: `source_item_cohort`
   - **Description**: The percentage of classified items in the ingested cohort that are relevant (core or adjacent).
   - **Formula**: $$\text{Relevance Rate} = \frac{\text{Classify Core} + \text{Classify Adjacent}}{\text{Total Classified}}$$ where `source_item.created_at` is within the lookback window and the item has a row in `classification_result`.
   - **Data Source**: `classification_result`, `source_item`
   - **Direct Dimensions**: `source_item_id`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

5. **Curation Approval Rate**
   - **Window Semantic**: `source_item_cohort`
   - **Description**: The percentage of curated items in the ingested cohort that are approved.
   - **Formula**: $$\text{Curation Approval Rate} = \frac{\text{Curate Approved Count}}{\text{Total Curated Items}}$$ where `source_item.created_at` is within the lookback window and the item has a row in `curation_decision`.
   - **Data Source**: `curation_decision`, `source_item`
   - **Direct Dimensions**: `source_item_id`, `decision_actor`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

6. **Overall Yield**
   - **Window Semantic**: `source_item_cohort`
   - **Description**: The total conversion rate from ingestion to approved curation for the cohort.
   - **Formula**: $$\text{Overall Yield} = \frac{\text{Curate Approved Count}}{\text{Total Ingested Items}}$$ where `source_item.created_at` is within the lookback window.
   - **Data Source**: `curation_decision`, `source_item`
   - **Direct Dimensions**: `source_item_id`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

7. **Translation Success Rate**
   - **Window Semantic**: `event_time`
   - **Description**: The success rate of translation attempts executed within the window.
   - **Formula**: $$\text{Translation Success Rate} = \frac{\text{Successful Translations (translation\_status = 'completed')}}{\text{Total Translation Attempts (translation\_status IN ('completed', 'failed', 'stale'))}}$$ where `translation_output.updated_at` is within the lookback window.
   - **Data Source**: `translation_output`
   - **Direct Dimensions**: `source_item_id`, `language_code`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

8. **Translation Latency**
   - **Window Semantic**: `event_time`
   - **Description**: Average processing latency for translations completed within the window.
   - **Formula**: $$\text{Average Latency} = \text{Average}(\text{translation\_output.translated\_at} - \text{approved\_content\_record.approved\_at})$$ where `translation_output.translated_at` is within the lookback window.
   - **Data Source**: `translation_output`, `approved_content_record`
   - **Direct Dimensions**: `source_item_id`, `language_code`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

9. **Publish Count**
   - **Window Semantic**: `event_time`
   - **Description**: Total number of successfully published content items within the window.
   - **Formula**: Count of records in the `publish_record` table where `publish_record.published_at` is within the lookback window.
   - **Data Source**: `publish_record`
   - **Direct Dimensions**: `source_item_id`
   - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)

10. **Workload Volume Proxies**
    *   **A. Classification Character Volume Proxy**
        - **Window Semantic**: `source_item_cohort`
        - **Description**: Estimate of processed character volume for classification of the ingested cohort.
        - **Formula**: Sum of `length(source_item.title) + source_item_text.sanitized_text_length` where `source_item.created_at` is within the lookback window and `source_item_text.is_low_context = 0`.
        - **Data Source**: `source_item`, `source_item_text`
        - **Direct Dimensions**: `source_item_id`
        - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
    *   **B. Curation Character Volume Proxy**
        - **Window Semantic**: `source_item_cohort`
        - **Description**: Estimate of character volume reviewed by the curation stage for the ingested cohort.
        - **Formula**: Sum of `length(source_item.title) + source_item_text.sanitized_text_length` where `source_item.created_at` is within the lookback window and the item has a row in `curation_decision`.
        - **Data Source**: `curation_decision`, `source_item`, `source_item_text`
        - **Direct Dimensions**: `source_item_id`, `decision_actor`
        - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
        - **Notes**: This is an input-volume proxy only. It does not capture prompt wrapper size, generated output length, token billing, or actual human reading time.
    *   **C. Translation Character Volume Proxy**
        - **Window Semantic**: `source_item_cohort`
        - **Description**: Estimate of processed character volume for translation workload of the ingested cohort.
        - **Formula**: Sum of `length(approved_content_record.display_title) + length(approved_content_record.content_body)` where `source_item.created_at` is within the lookback window and the item has a row in `translation_output`.
        - **Data Source**: `approved_content_record`, `source_item`, `translation_output`
        - **Direct Dimensions**: `source_item_id`, `content_language_code`
        - **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`), `language_code` (via joining matching `translation_output` rows)

---

## Metric Promotion Lifecycle

To prevent KPI inflation and maintain a stable dashboard, the following rules govern changes to this list:
1. **No direct additions**: New metrics cannot be directly added to `MVP_METRICS.md`.
2. **Incubation**: New metrics must start in [EXPLORATORY_SIGNALS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/EXPLORATORY_SIGNALS.md) or [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md).
3. **Promotion criteria**: A metric can only be promoted to MVP if it is regularly tracked, decision-useful, and supported by stable canonical schemas.
