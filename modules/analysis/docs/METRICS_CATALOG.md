# Metrics Catalog

This document catalog lists all stable metrics defined for the `analysis` module. Each entry outlines the name, purpose, window basis, formula, data source, dimensions (direct and derived), update frequency, and relevant implementation notes.

---

## Time Window Semantics Reference

All metrics in this catalog (except rolling snapshots) are filtered by the lookback window configured during execution (default: 7 days). They use one of the following window semantics:

- **`source_item_cohort`**: The lookback window filters the base ingestion records (`source_item.created_at`). Downstream events/states are linked back to this cohort. Used for funnel and conversion analytics to maintain mathematical consistency.
- **`event_time`**: The lookback window filters events using the timestamp of the metric's own primary event table (e.g. `fetch_attempt.created_at`, `translation_output.translated_at`). Used for operational and health monitoring.
- **`rolling_snapshot`**: Reflects the current state at the time of query (no lookback window filtering).

---

## 1. Source Health Metrics

### 1.1 Fetch Success Rate (MVP)
*   **Purpose**: Monitor reliability of connection to external feeds.
*   **Window Basis**: `event_time`
*   **Formula**: $$\text{Fetch Success Rate} = \frac{\text{Successful Fetch Attempts (fetch\_attempt.outcome = 'success')}}{\text{Total Fetch Attempts}}$$ where `fetch_attempt.created_at` is within the lookback window.
*   **Data Source**: `fetch_attempt`
*   **Direct Dimensions**: `source_id`, `fetch_attempt_id`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run (typically daily or ad-hoc).
*   **Notes**: Used as the primary filter for fetch health isolation before content quality analysis.

### 1.2 Run Success Rate
*   **Purpose**: Evaluate the execution reliability at the fetch-run level.
*   **Window Basis**: `event_time`
*   **Formula**: $$\text{Run Success Rate} = \frac{\text{Successful Source Attempts in fetch\_run}}{\text{Attempted Source Count in fetch\_run}}$$ where `fetch_run.started_at` is within the lookback window.
*   **Data Source**: `fetch_run`
*   **Direct Dimensions**: `fetch_run_id`, `run_scope`, `trigger_type`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Helps detect overall infrastructure or network failures affecting multiple feeds.

### 1.3 Error Categorization Rate
*   **Purpose**: Pinpoint feed issues (connection, Cloudflare/anti-bot, parsing).
*   **Window Basis**: `event_time`
*   **Formula**: Group counts of failed fetch attempts by error class where `fetch_attempt.created_at` is within the lookback window.
*   **Data Source**: `fetch_attempt`
*   **Direct Dimensions**: `source_id`, `error_class`, `http_status`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Relies on `fetch_attempt.error_class` and `fetch_attempt.error_detail`.

### 1.4 Rolling Source Health Snapshot
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

### 2.1 Ingest Volume (MVP)
*   **Purpose**: Track total raw volume of items pulled into the system.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Count of records in `source_item` where `source_item.created_at` is within the lookback window.
*   **Data Source**: `source_item`
*   **Direct Dimensions**: `source_item_id`, `source_id`
*   **Derived Dimensions**: None
*   **Update Frequency**: Executed per CLI run.

### 2.2 Low-Context Bypass Rate (MVP)
*   **Purpose**: Monitor sources producing thin snippet content that bypasses LLM classification.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Low-Context Bypass Rate} = \frac{\text{Low-Context Ingested Items (source\_item\_text.is\_low\_context = 1)}}{\text{Total Ingested}}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `source_item_text`, `source_item`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 2.3 Relevance Rate (MVP)
*   **Purpose**: Measure the alignment of ingested feed items with core/adjacent topics.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Relevance Rate} = \frac{\text{Classify Core} + \text{Classify Adjacent}}{\text{Total Classified (items with a row in classification\_result)}}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `classification_result`, `source_item`
*   **Direct Dimensions**: `source_item_id` (from `classification_result`)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 2.4 Curation Approval Rate (MVP)
*   **Purpose**: Measure editorial value of filtered items.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Curation Approval Rate} = \frac{\text{Curate Approved Count}}{\text{Total Curated Items (items with a row in curation\_decision)}}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `decision_actor` (natively `curation_decision.decision_actor` is 'system' or 'operator')
*   **Derived Dimensions**:
    *   `source_id` (via joining `source_item` on `source_item_id`)
    *   `editor` (if approved, via parsing JSON metadata from `approved_content_record.author_metadata` for the `'editor'` key)
*   **Update Frequency**: Executed per CLI run.

### 2.5 Overall Yield (MVP)
*   **Purpose**: Measure final throughput from ingest to approval.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Overall Yield} = \frac{\text{Curate Approved Count}}{\text{Total Ingested (items with a row in source\_item)}}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 2.6 Curation Rejection Mix (Phase 2)
*   **Purpose**: Track editorial overhead (e.g. discard vs rewrite decisions).
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Group count of rejected curation decisions by downstream action where `source_item.created_at` is within the lookback window.
*   **Data Source**: `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `downstream_action` (e.g. `edit_rewrite`, `reject_discard`)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 2.7 Publish Count (MVP)
*   **Purpose**: Track total number of successfully published content items.
*   **Window Basis**: `event_time`
*   **Formula**: Count of records in the `publish_record` table where `publish_record.published_at` is within the lookback window.
*   **Data Source**: `publish_record`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

---

## 3. Source Quality & Processing Efficiency

### 3.1 Workload Volume Proxies

> [!NOTE]
> **Workload Volume Proxies Comparison & Conceptual Boundaries**:
> To ensure consistent interpretation of workload across stages, engineers and analysts must respect these conceptual boundaries:
> 1.  **Classification Character Volume Proxy** = Representing the **classify stage input volume** (all raw feeds after passing basic low-context checks, i.e., `is_low_context = 0`).
> 2.  **Curation Character Volume Proxy** = Representing the **curate stage input volume** (only the high-relevance subset of items filtered by the classification stage that reached a curation decision).
> 3.  **Translation Character Volume Proxy** = Representing the downstream **translation workload**, which is conceptually different from the ingest-text proxies because it is calculated using the finalized, edited, and approved mother-draft text (`approved_content_record`) rather than the sanitized ingest text (`source_item_text`).

#### 3.1.1 Classification Character Volume Proxy (MVP)
*   **Purpose**: Track raw classification workload.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Sum of `length(source_item.title) + source_item_text.sanitized_text_length` where `source_item.created_at` is within the lookback window and `source_item_text.is_low_context = 0`.
*   **Data Source**: `source_item`, `source_item_text`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

#### 3.1.2 Curation Character Volume Proxy (MVP)
*   **Purpose**: Estimate input character volume reviewed by the curation stage for items that reached a recorded curation decision.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Sum of `length(source_item.title) + source_item_text.sanitized_text_length` where `source_item.created_at` is within the lookback window and the item has a row in `curation_decision`.
*   **Data Source**: `curation_decision`, `source_item`, `source_item_text`
*   **Direct Dimensions**: `source_item_id`, `decision_actor` (system vs operator)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: This is an input-side proxy and does not represent output token generation, prompt templates, or human editor reading speed.

#### 3.1.3 Translation Character Volume Proxy (MVP)
*   **Purpose**: Estimate relative API translation workload.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Sum of `length(approved_content_record.display_title) + length(approved_content_record.content_body)` where `source_item.created_at` is within the lookback window and the item has a row in `translation_output`.
*   **Data Source**: `approved_content_record`, `source_item`, `translation_output`
*   **Direct Dimensions**: `source_item_id`, `content_language_code`
*   **Derived Dimensions**: 
    *   `source_id` (via joining `source_item` on `source_item_id`)
    *   `language_code` (via joining matching `translation_output` rows when grouping by translation target language)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: The default report output represents the **Target Language Execution Workload** (actual execution attempts where translation was initiated, matching rows in `translation_output` with status `'completed'`, `'failed'`, or `'stale'`). This ensures alignment with classification and curation proxies which track processed volume.

### 3.2 Classification Filtering Overhead
*   **Purpose**: Evaluate source efficiency (ratio of inputs needed for one output).
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Classification Filtering Overhead} = \frac{\text{Total Classified}}{\text{Curate Approved}}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `classification_result`, `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 3.3 Content Density Distribution
*   **Purpose**: Characterize source informational quality (thin vs dense content).
*   **Window Basis**: `source_item_cohort`
*   **Formula**: Distribution of `classification_result.content_density` (low, medium, high) where `source_item.created_at` is within the lookback window.
*   **Data Source**: `classification_result`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `content_density`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 3.4 Approval Rate by Content Density (Phase 2)
*   **Purpose**: Test whether denser content is more publishable.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Approval Rate (Density } x) = \frac{\text{Approved Items with Density } x}{\text{Total Items with Density } x}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `classification_result`, `curation_decision`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `content_density` (from `classification_result`)
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 3.5 Unique Contribution Rate (Phase 2)
*   **Purpose**: Measure uniqueness of source content after deduplication.
*   **Window Basis**: `source_item_cohort`
*   **Formula**: $$\text{Unique Contribution Rate} = \frac{\text{Curate Approved (Undeduped)}}{\text{Total Ingested}}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `curation_decision`, `source_item`, `ingest_dedup_marker`
*   **Direct Dimensions**: `source_item_id`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.
*   **Notes**: Dedup checks must reference `ingest_dedup_marker` and `source_item.ingest_dedup_key` rather than ad-hoc titles.

---

## 4. Translation Performance Metrics

### 4.1 Translation Success Rate (MVP)
*   **Purpose**: Monitor the reliability of translation execution.
*   **Window Basis**: `event_time`
*   **Formula**: $$\text{Translation Success Rate} = \frac{\text{Successful Translations (translation\_status = 'completed')}}{\text{Total Translation Attempts (translation\_status IN ('completed', 'failed', 'stale'))}}$$ where `translation_output.updated_at` is within the lookback window.
*   **Data Source**: `translation_output`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 4.2 Translation Latency (MVP)
*   **Purpose**: Track time to publish from approval to completed translation.
*   **Window Basis**: `event_time`
*   **Formula**: $$\text{Average Latency} = \text{Average}(\text{translation\_output.translated\_at} - \text{approved\_content\_record.approved\_at})$$ where `translation_output.translated_at` is within the lookback window.
*   **Data Source**: `translation_output`, `approved_content_record`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 4.3 Translation Completion Rate
*   **Purpose**: Track how many approved items actually get translated.
*   **Window Basis**: `source_item_cohort`
*   **Formula**:
    *   **Global Completion Rate**: $$\text{Translation Completion Rate} = \frac{\text{Unique approved items with translation\_status = 'completed'}}{\text{Unique items in approved\_content\_record}}$$ where `source_item.created_at` is within the lookback window.
    *   **Per-Locale Completion Rate (for language } L\text{)}**: $$\text{Translation Completion Rate}_L = \frac{\text{Unique items with language\_code = } L \text{ and translation\_status = 'completed'}}{\text{Unique items in approved\_content\_record}}$$ where `source_item.created_at` is within the lookback window.
*   **Data Source**: `approved_content_record`, `translation_output`, `source_item`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 4.4 Translation Stale Rate
*   **Purpose**: Identify items lost due to delays or updates during the translation queue.
*   **Window Basis**: `event_time`
*   **Formula**: Percentage of items marked stale (i.e. `translation_status = 'stale'`) where `translation_output.updated_at` (or timestamp marked stale) is within the lookback window.
*   **Data Source**: `translation_output`
*   **Direct Dimensions**: `source_item_id`, `language_code`, `translation_status`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

### 4.5 Translation Character Share by Language (Phase 2)
*   **Purpose**: Compare processing load between locales and time windows.
*   **Window Basis**: `event_time`
*   **Formula**: Sum of `(length(translation_output.display_title) + length(translation_output.content))` where `translation_output.updated_at` is within the lookback window and `translation_status = 'completed'`, grouped by `translation_output.language_code`.
*   **Data Source**: `translation_output`
*   **Direct Dimensions**: `source_item_id`, `language_code`
*   **Derived Dimensions**: `source_id` (via joining `source_item` on `source_item_id`)
*   **Update Frequency**: Executed per CLI run.

---

## 5. Data Grounding Schema Mapping
The metrics above are supported by these canonical tables in [canonical.db](file:///C:/Users/user/Documents/exopolitics/data/canonical.db):
*   `fetch_run`: Run-level outcomes.
*   `fetch_attempt`: Individual feed outcomes, HTTP status, and connection errors.
*   `source_state`: Rolling failures and quarantine status.
*   `source_item`: Ingested item identities and dedup keys.
*   `source_item_text`: Sanitized lengths and context classification flags.
*   `classification_result`: Content density, relevance classes, and additional signals.
*   `curation_decision`: Editor approvals, rejections, and downstream actions.
*   `approved_content_record`: Core drafts, titles, and approval timestamps.
*   `translation_output`: Language codes, translated timestamps, text contents, and retries.
*   `publish_record`: Published items and final slugs.

### 5.1 Source Metadata Mapping Constraint (No `source` Table)
Because `canonical.db` does not maintain a relational `source` table, the `analysis` module must resolve source metadata (e.g. source title, enabled status, category) by reading the external configuration files:
*   [sources.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/sources.yaml): Contains individual source identifiers (`id`), crawl groups (`fetch_group`), schedule categories (`schedule_class`), and `category_id`.
*   [categories.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/categories.yaml): Maps `category_id` to descriptive titles (e.g. `Government Policy & Official Disclosure`) and category enabled status.

The mapping of `source_id` in database rows to these attributes must be done in memory. Under no circumstances should runtime logic expect SQL joins to resolve source or category attributes directly.
