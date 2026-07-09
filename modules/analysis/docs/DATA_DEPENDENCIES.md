# Data Dependencies

This document specifies the read-only data dependencies, database schemas, configuration files, and memory-join rules required by the `analysis` module.

---

## 1. Canonical Database Read Dependencies

The `analysis` module queries `data/canonical.db` to calculate metrics. It is strictly read-only and must never insert, update, or delete rows in these tables.

### 1.1 Database Table Schema Mappings

| Table Name | Required Columns | Purpose / Used in Metrics |
| :--- | :--- | :--- |
| `fetch_run` | `fetch_run_id`, `started_at`, `run_scope`, `trigger_type` | Run Success Rate |
| `fetch_attempt` | `fetch_attempt_id`, `fetch_run_id`, `source_id`, `started_at`, `ended_at`, `outcome`, `error_class`, `http_status` | Fetch Success Rate, Error Categorization, Fetch Execution Latency |
| `source_state` | `source_id`, `health_status`, `consecutive_failures`, `last_http_status`, `last_error_class` | Rolling Source Health Snapshot |
| `source_item` | `source_item_id`, `source_id`, `fetched_at`, `published_at`, `ingest_dedup_key` | Ingest Volume, Cohort definition, Feed Freshness Delay |
| `source_item_text` | `source_item_id`, `sanitized_text_length`, `is_low_context`, `low_context_reason` | Low-Context Bypass Rate, Workload Volume Proxies, Low-Context Reason Distribution |
| `classification_result` | `source_item_id`, `classified_at`, `topic_class`, `content_density`, `additional_signals` | Relevance Rate, Content Density Distribution, Classification Delay |
| `curation_decision` | `source_item_id`, `curated_at`, `decision_actor`, `downstream_action` | Curation Approval Rate, Curation Rejection Mix, Curation Delay |
| `approved_content_record` | `parent_content_id`, `source_item_id`, `approved_at`, `display_title`, `content_body`, `content_language_code` | Overall Yield, Translation Completion Rate, Workload Proxies |
| `translation_output` | `translation_output_id`, `parent_content_id`, `language_code`, `translation_status`, `model_name`, `translated_at`, `updated_at`, `display_title`, `content` | Translation Success Rate, Translation Delay, Workload Proxies |
| `publish_record` | `publish_record_id`, `source_item_id`, `first_published_at`, `slug` | Publish Count, Pipeline Lead Time |
| `publish_language_status` | `publish_record_id`, `language_code`, `publish_status`, `published_at` | Publish Delay, Language Coverage Rate |

---

## 2. External Configuration Dependencies

Since the canonical SQLite database does not maintain a relational table for sources or categories, the `analysis` module depends on external configuration files owned by the `ingest` module.

### 2.1 File Mappings & Paths
*   **Sources Config**: [sources.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/sources.yaml)
    *   *Fields Used*: `id`, `title`, `xml_url`, `html_url`, `category_id`, `enabled`, `fetch_group`, `schedule_class`.
*   **Categories Config**: [categories.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/categories.yaml)
    *   *Fields Used*: Dictionary key (resolves as category ID), `name`, `slug`, `enabled`.

### 2.2 Memory-Join Rules
1.  **Strictly In-Memory**: Direct SQL joins between the SQLite database and source details are impossible. Source metadata resolution must be completed in application memory.
2.  **Lookup Dictionary Initialization**: Upon execution, the module must parse `sources.yaml` and `categories.yaml` into runtime lookup dictionaries.
3.  **Null & Missing Source Handlers**:
    *   If a database row contains a `source_id` that is not present in `sources.yaml`, the lookup must return a placeholder (e.g. `Title: "Unknown Source (ID: X)"`) and assign the flag `[INSUFFICIENT_DATA]`.
    *   The CLI must not fail or crash due to missing configuration keys. It must continue report generation for other valid sources.

---

## 3. Allowed Derived Dimensions

To support reporting breakdowns without database pollution, the `analysis` module is permitted to calculate and group results by the following derived dimensions in memory:

*   **Source Category**: Resolved by joining `source_id` (from DB) to `category_id` (from `sources.yaml`), then mapping to category names in `categories.yaml`.
*   **Crawl Cadence Class**: Grouping metrics by the source's `schedule_class` (e.g., `high_cadence`, `daily`) to evaluate whether fetch frequency matches source yield.
*   **Source Quality Quadrant**: The categorical label (`golden_source`, `dead_weight`, etc.) calculated at runtime using the rules in [DECISION_MODELS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DECISION_MODELS.md).
