# Metrics Catalog

This document is the authoritative catalog for all metrics defined for the `analysis` module. Each entry outlines the name, status (MVP Core or Phase 2/Exploratory), purpose, window basis, formula, data source, dimensions, update frequency, and relevant implementation notes.

---

## Time Window Semantics Reference

All metrics in this catalog (except rolling snapshots) are filtered by the lookback window configured during execution (default: 7 days). They use one of the following window semantics:

- **`source_item_cohort`**: The lookback window filters the base ingestion records (`source_item.fetched_at`). Downstream events/states are linked back to this cohort. Used for funnel and conversion analytics to maintain mathematical consistency.
- **`event_time`**: The lookback window filters events using the timestamp of the metric's own primary event table (e.g. `fetch_attempt.started_at`, `translation_output.updated_at`). Used for operational and health monitoring.
- **`rolling_snapshot`**: Reflects the current state at the time of query (no lookback window filtering).

---

## 1. Source Health Metrics

### 1.1 Fetch Success Rate `[MVP]`
*   **Purpose**: Monitor reliability of connection to external feeds.
*   **Window Basis**: `event_time`
*   **Formula**: $$\text{Fetch Success Rate} = \frac{\text{Successful Fetch Attempts (fetch\_attempt.outcome = 'success')}}{\text{Total Fetch Attempts}}$$ where `fetch_attempt.started_at` is within the lookback window.
*   **Data Source**: `fetch_attempt`
*   **Direct Dimensions**: `source_id`, `fetch_attempt_id`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Used as the primary filter for fetch health isolation before content quality analysis.

### 1.2 Run Success Rate `[Phase 1 / Stable]`
*   **Purpose**: Evaluate the execution reliability at the fetch-run level.
*   **Window Basis**: `event_time`
*   **Formula**: $$\text{Run Success Rate} = \frac{\text{fetch\_run.succeeded\_source\_count}}{\text{NULLIF(fetch\_run.attempted\_source\_count, 0)}}$$ where `fetch_run.started_at` is within the lookback window.
*   **Data Source**: `fetch_run`
*   **Direct Dimensions**: `fetch_run_id`, `run_scope`, `trigger_type`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Helps detect overall infrastructure or network failures affecting multiple feeds. Uses the real schema fields `succeeded_source_count` and `attempted_source_count`.

### 1.3 Error Categorization Rate `[Phase 1 / Stable]`
*   **Purpose**: Pinpoint feed issues (connection, Cloudflare/anti-bot, parsing).
*   **Window Basis**: `event_time`
*   **Formula**: Group counts of failed fetch attempts by error class where `fetch_attempt.started_at` is within the lookback window.
*   **Data Source**: `fetch_attempt`
*   **Direct Dimensions**: `source_id`, `error_class`, `http_status`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Relies on `fetch_attempt.error_class` and `fetch_attempt.http_status` to categorize and group failures.

### 1.4 Rolling Source Health Snapshot `[Phase 1 / Stable]`
*   **Purpose**: Identify quarantined or consistently failing sources.
*   **Window Basis**: `rolling_snapshot`
*   **Formula**: Current value of consecutive failures and health status in the table.
*   **Data Source**: `source_state`
*   **Direct Dimensions**: `source_id`, `health_status`
*   **Derived Dimensions**: None
*   **Update Frequency**: Daily snapshot.
*   **Notes**: Utilizes `source_state.consecutive_failures`, `source_state.last_http_status`, and `source_state.last_error_class`.

---

## 2. Pipeline Funnel & Conversion Metrics

### 2.1 Ingest Volume `[MVP]`
*   **Purpose**: Track total raw volume of items pulled into the system.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Count of records in `source_item` where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `source_item`
*   **Direct Dimensions**: `source_item_id`, `source_id`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run.

### 2.2 Low-Context Bypass Rate `[MVP]`
*   **Purpose**: Monitor sources producing thin snippet content that bypasses LLM classification.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Low-Context Bypass Rate} = \frac{\text{Low-Context Ingested Items (source\_item\_text.is\_low\_context = 1)}}{\text{Total Ingested}}$$ where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `source_item_text`, `source_item`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

#### 2.2.1 Low-Context Reason Distribution `[Phase 2 / Catalog]`
*   **Purpose**: Diagnose the specific causes of low-context bypasses to distinguish between parser/crawler failures (e.g. `missing_body`) and inherent source properties (e.g. `title_only`).
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Group count of low-context items (`source_item_text.is_low_context = 1`) by `source_item_text.low_context_reason` where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `source_item_text`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `low_context_reason`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: 
    *   This is an auxiliary diagnostic metric. `low_context_reason` is intended for diagnostic interpretation of low-context items and must not be treated as an independent quality score or automated disable trigger.
    *   Report layers may derive percentages from these grouped counts, but the canonical metric contract is count-based to avoid ambiguity.

### 2.3 Relevance Rate `[MVP]`
*   **Purpose**: Measure the alignment of ingested feed items with core/adjacent topics.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Relevance Rate} = \frac{\text{Classify Core} + \text{Classify Adjacent}}{\text{NULLIF(Total Classified (items with a row in classification\_result), 0)}}$$ where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `classification_result`, `source_item`
*   **Direct Dimensions**: `source_item_id` (from `classification_result`)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Under the V2 architecture, `classification_result` contains only items that actually entered classification (low-context bypass items do not receive rows). Thus, this rate is defined strictly over the actual classified population.

#### 2.3.1 Topic Class Breakdown `[MVP]`
*   **Purpose**: Preserve the full distribution of `classification_result.topic_class` for each source instead of flattening all relevant outcomes into a single scalar.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: For each source, compute the proportion of classified items falling into `core`, `adjacent`, `irrelevant`, and `unknown`, where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `classification_result`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `topic_class`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: This breakdown is a required output field for `analyze-sources`. `Relevance Rate` is the aggregate scalar `core + adjacent`, while `topic_class_breakdown` preserves the underlying mix used for operator interpretation.

### 2.4 Curation Approval Rate `[MVP]`
*   **Purpose**: Measure editorial value of filtered items.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Curation Approval Rate} = \frac{\text{Curate Approved Count}}{\text{Total Curated Items (items with a row in curation\_decision)}}$$ where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `decision_actor` (natively `curation_decision.decision_actor` is 'system' or 'operator')
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 2.5 Overall Yield `[MVP]`
*   **Purpose**: Measure final throughput from ingest to finalized approval.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Overall Yield} = \frac{\text{Approved Content Count (items with a row in approved\_content\_record)}}{\text{Total Ingested (items with a row in source\_item)}}$$ where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `approved_content_record`, `source_item`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 2.6 Curation Rejection Mix `[Phase 2]`
*   **Purpose**: Track editorial overhead (e.g. discard vs rewrite decisions).
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Group count of rejected curation decisions by downstream action where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `downstream_action` (e.g. `edit_rewrite`, `reject_discard`)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 2.7 Publish Count `[MVP]`
*   **Purpose**: Track total number of successfully published content items.
*   **Window Basis**: `event_time`
*   **Formula**: Count of records in the `publish_record` table where `publish_record.first_published_at` is within the lookback window.
*   **Data Source**: `publish_record`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

---

## 3. Source Quality & Processing Efficiency

### 3.1 Workload Volume Proxies `[MVP]`

> [!NOTE]
> **Workload Volume Proxies Comparison & Conceptual Boundaries**:
> To ensure consistent interpretation of workload across stages, engineers and analysts must respect these conceptual boundaries:
> 1.  **Classification Character Volume Proxy** = Representing the **classify stage input volume** (all raw feeds after passing basic low-context checks, i.e., `is_low_context = 0`).
> 2.  **Curation Character Volume Proxy** = Representing the **curate stage input volume** (only the high-relevance subset of items filtered by the classification stage that reached a curation decision).
> 3.  **Translation Character Volume Proxy** = Representing the downstream **translation workload**, which is conceptually different from the ingest-text proxies because it is calculated using the finalized, edited, and approved mother-draft text (`approved_content_record`) rather than the sanitized ingest text (`source_item_text`).

#### 3.1.1 Classification Character Volume Proxy `[MVP]`
*   **Purpose**: Track raw classification workload.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Sum of `length(source_item.title) + source_item_text.sanitized_text_length` where `source_item.fetched_at` is within the lookback window and `source_item_text.is_low_context = 0`.
*   **Data Source**: `source_item`, `source_item_text`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

#### 3.1.2 Curation Character Volume Proxy `[MVP]`
*   **Purpose**: Estimate input character volume reviewed by the curation stage for items that reached a recorded curation decision.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Sum of `length(source_item.title) + source_item_text.sanitized_text_length` where `source_item.fetched_at` is within the lookback window and the item has a row in `curation_decision`.
*   **Data Source**: `curation_decision`, `source_item`, `source_item_text`
*   **Direct Dimensions**: `source_item_id`, `decision_actor` (system vs operator)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: This is an input-side proxy and does not represent output token generation, prompt templates, or human editor reading speed.

#### 3.1.3 Translation Character Volume Proxies (Dual Metrics) `[MVP]`
*   **Purpose**: Estimate active API translation workload and total queue workload.
*   **Window Basis**: `source_item_cohort`
*   **Formula (Recorded Workload)**: Sum of `length(approved_content_record.display_title) + length(approved_content_record.content_body)` where `source_item.fetched_at` is within the lookback window and the item has a row in `translation_output` with `model_name != 'bypass'` (excludes self-translation bypass).
*   **Formula (Intended Workload Upper Bound)**: Sum of `length(approved_content_record.display_title) + length(approved_content_record.content_body)` multiplied by target language count minus one (excluding bypass).
*   **Data Source**: `approved_content_record`, `source_item`, `translation_output`
*   **Direct Dimensions**: `source_item_id`, `content_language_code`
*   **Derived Dimensions**: 
    *   `source_id` (via joining `source_item` on `source_item_id`)
    *   `language_code`
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Filtering out `'bypass'` in the recorded workload ensures we only track LLM API-incurred costs.

### 3.2 Classification Filtering Overhead `[Phase 2 / Catalog]`
*   **Purpose**: Evaluate source efficiency (ratio of inputs needed for one output).
*   **Status**: Downgraded to Catalog/Exploratory for Phase 1. Excluded from the MVP top-level dashboard.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Classification Filtering Overhead} = \frac{\text{Total Classified}}{\text{NULLIF(Curate Approved, 0)}}$$ where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `classification_result`, `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 3.3 Content Density Distribution `[Phase 2 / Catalog]`
*   **Purpose**: Characterize source informational quality (thin vs dense content).
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Distribution of `classification_result.content_density` (low, medium, high) where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `classification_result`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `content_density`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 3.4 Approval Rate by Content Density `[Phase 2]`
*   **Purpose**: Test whether denser content is more publishable.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Approval Rate (Density } x) = \frac{\text{Approved Items with Density } x}{\text{NULLIF(Total Items with Density } x, 0)}$$ where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `classification_result`, `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `content_density` (from `classification_result`)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 3.5 Unique Contribution Rate `[Phase 2 / Defer]`
*   **Purpose**: Measure uniqueness of source content after deduplication.
*   **Status**: Defer to Phase 2 / exploratory pending database validation of cross-source duplicates. Excluded from Phase 1 MVP.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Unique Contribution Rate} = \frac{\text{Curate Approved (Undeduped)}}{\text{Total Ingested}}$$ where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `curation_decision`, `source_item`, `ingest_dedup_marker`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Dedup checks must reference `ingest_dedup_marker` and `source_item.ingest_dedup_key` rather than ad-hoc titles.

---

## 4. Pipeline Lead Time & Stage Latency Suite

### 4.1 Pipeline Lead Time (E2E Latency) `[MVP]`
*   **Purpose**: Monitor the end-to-end timeliness and delivery speed of content from ingestion fetch time (source_item.fetched_at) to publication.
*   **Window Basis**: `source_item_cohort`
*   **Metric Type**: `end_to_end_lead_time`
*   **Formula**: Average, Median (p50), and 90th percentile (p90) of `strftime('%s', publish_record.first_published_at) - strftime('%s', source_item.fetched_at)` where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `source_item`, `publish_record`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: The top-level SLA metric. High p90 values indicate major delivery bottlenecks. Latency math uses `strftime('%s', ...)` epoch conversion for SQLite compatibility.

### 4.2 Pipeline Stage Latency Suite (Diagnostic Metrics) `[MVP]`
To diagnose end-to-end bottlenecks, the pipeline is segmented into stage-specific latencies. All metrics in this suite calculate the Average, Median (p50), and 90th percentile (p90) statistics, and are explicitly classified into execution, freshness, or queue delay types. Note: When reported together as part of the E2E latency breakdown in the funnel report (analyze-funnel), all of these stage metrics—including Fetch Execution Latency—are computed strictly using the source_item_cohort basis to ensure direct statistical comparability.

#### 4.2.1 Feed Freshness Delay `[MVP]`
*   **Purpose**: Measure lag between external content publication and ingestion.
*   **Window Basis**: `source_item_cohort`
*   **Delay Class**: `freshness_delay`
*   **Formula**: `strftime('%s', source_item.fetched_at) - strftime('%s', source_item.published_at)` where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `source_item`
*   **Direct Dimensions**: `source_item_id`, `source_id`
*   **Notes**: Reflects crawling frequency efficiency. Highly dependent on external site feed updates. Latency math uses SQLite epoch conversion.

#### 4.2.2 Fetch Execution Latency `[MVP]`
*   **Purpose**: Monitor network retrieval speed.
*   **Window Basis**: `event_time`
*   **Delay Class**: `execution_latency`
*   **Formula**: `strftime('%s', fetch_attempt.ended_at) - strftime('%s', fetch_attempt.started_at)` where `fetch_attempt.started_at` is within the lookback window.
*   **Data Source**: `fetch_attempt`
*   **Direct Dimensions**: `source_id`, `fetch_attempt_id`
*   **Notes**: Captures active HTTP download and connection speed. Latency math uses SQLite epoch conversion.

#### 4.2.3 Classification Delay `[MVP]`
*   **Purpose**: Measure queue wait and LLM classification processing.
*   **Window Basis**: `source_item_cohort`
*   **Delay Class**: `queue_delay` + `execution_latency`
*   **Formula**: `strftime('%s', classification_result.classified_at) - strftime('%s', source_item.fetched_at)` where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `source_item`, `classification_result`
*   **Direct Dimensions**: `source_item_id`
*   **Notes**: Measures time spent waiting in scheduling queue plus the active classification LLM call duration. Latency math uses SQLite epoch conversion.

#### 4.2.4 Curation Delay `[MVP]`
*   **Purpose**: Monitor curation queue lag and operator review efficiency.
*   **Window Basis**: `source_item_cohort`
*   **Delay Class**: `queue_delay`
*   **Formula**: `strftime('%s', curation_decision.curated_at) - strftime('%s', classification_result.classified_at)` where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `source_item`, `classification_result`, `curation_decision`
*   **Direct Dimensions**: `source_item_id`, `decision_actor` (system vs operator)
*   **Notes**: Heavily long-tailed for operator decisions. Vital for measuring human curation queue bottleneck. Latency math uses SQLite epoch conversion.

#### 4.2.5 Translation Delay `[MVP]`
*   **Purpose**: Measure translation queue wait and LLM translation processing.
*   **Window Basis**: `source_item_cohort`
*   **Delay Class**: `queue_delay` + `execution_latency`
*   **Formula**: `strftime('%s', translation_output.translated_at) - strftime('%s', approved_content_record.approved_at)` where `source_item.fetched_at` is within the lookback window and `translation_output.model_name != 'bypass'`.
*   **Data Source**: `approved_content_record`, `translation_output`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Notes**: Tracks time from curation approval to translation output generation. Excludes self-translation bypass records (where `model_name = 'bypass'`) to prevent 0-second bias. Latency math uses SQLite epoch conversion.

#### 4.2.6 Publish Delay `[MVP]`
*   **Purpose**: Track output file generation and static asset deployment speed.
*   **Window Basis**: `source_item_cohort`
*   **Delay Class**: `queue_delay`
*   **Formula**: `strftime('%s', publish_language_status.published_at) - strftime('%s', translation_output.translated_at)` where `source_item.fetched_at` is within the lookback window.
*   **Data Source**: `translation_output`, `publish_language_status`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Notes**: Measures the lag associated with export rendering in the publish module. Latency math uses SQLite epoch conversion.

---

## 4.3 Translation Performance & Queue Metrics

### 4.3.1 Translation Success Rate `[MVP]`
*   **Purpose**: Monitor the reliability of translation execution.
*   **Window Basis**: `event_time`
*   **Formula**: $$\text{Translation Success Rate} = \frac{\text{Successful Translations (translation\_status = 'completed')}}{\text{Total Translation Attempts (translation\_status IN ('completed', 'failed', 'stale'))}}$$ where `translation_output.updated_at` is within the lookback window.
*   **Data Source**: `translation_output`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 4.3.2 Translation Completion Rate `[MVP]`
*   **Purpose**: Track the percentage of approved articles that have successfully completed translation across all required target languages.
*   **Window Basis**: `source_item_cohort`
*   **Formula**:
    *   **Global Completion Rate (Article-Level)**: $$\text{Translation Completion Rate} = \frac{\text{Approved Articles with All Target Translations Completed}}{\text{Total Approved Articles}}$$ where both numerator and denominator are bound to the ingestion cohort (`source_item.fetched_at` within lookback window).
    *   **Per-Locale Completion Rate (for language } L\text{)}**: $$\text{Translation Completion Rate}_L = \frac{\text{Completed Translations for Language } L}{\text{Total Approved Items (where source language } \ne L\text{)}}$$
*   **Data Source**: `approved_content_record`, `translation_output`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **SQL Pattern (Global)**:
    ```sql
    SELECT
        COUNT(DISTINCT CASE WHEN completed_count = required_translation_count THEN parent_content_id END) * 1.0
        / NULLIF(COUNT(DISTINCT parent_content_id), 0) AS translation_completion_rate
    FROM (
        SELECT
            acr.parent_content_id,
            COUNT(CASE WHEN tor.translation_status = 'completed' AND tor.model_name != 'bypass' THEN 1 END) AS completed_count,
            -- Dynamically count configured target languages excluding the source language of the article
            (SELECT COUNT(*) FROM json_each(:target_languages_json) WHERE value != acr.content_language_code) AS required_translation_count
        FROM approved_content_record acr
        JOIN source_item si ON acr.source_item_id = si.source_item_id
        LEFT JOIN translation_output tor ON acr.parent_content_id = tor.parent_content_id
        WHERE si.fetched_at BETWEEN :start AND :end
        GROUP BY acr.parent_content_id
    ) t;
    ```
*   **Notes**: The global article-level metric must compare each article's completed translation count against the number of required non-bypass target languages for that article. Implementations must not assume a fixed completed-row count if target language requirements vary by source language or runtime configuration. The query dynamically counts targets using a JSON array parameter `:target_languages_json` (e.g., `["en", "zh", "ja"]`) joined via `json_each`. Ratios are protected with `NULLIF` zero-denominator checks.

### 4.3.3 Translation Stale Rate `[Phase 2 / Catalog]`
*   **Purpose**: Identify items lost due to delays or updates during the translation queue.
*   **Status**: Downgraded to Catalog/Diagnostics for Phase 1. Excluded from the MVP top-level dashboard.
*   **Window Basis**: `event_time`
*   **Formula**: Percentage of items marked stale (i.e. `translation_status = 'stale'`) where `translation_output.updated_at` is within the lookback window.
*   **Data Source**: `translation_output`
*   **Direct Dimensions**: `source_item_id`, `language_code`, `translation_status`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 4.3.4 Translation Character Share by Language `[Phase 2]`
*   **Purpose**: Compare processing load between locales and time windows.
*   **Window Basis**: `event_time`
*   **Formula**: Sum of `(length(translation_output.display_title) + length(translation_output.content))` where `translation_output.updated_at` is within the lookback window and `translation_status = 'completed'`, grouped by `translation_output.language_code`.
*   **Data Source**: `translation_output`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
