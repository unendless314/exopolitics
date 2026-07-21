# Analysis Module Evaluation Notes & Known Issues

This note documents findings, discrepancies, and optimization recommendations for the `analysis` module.

---

## 1. Funnel Report Reconciliation Discrepancy

### Observation
In [PIPELINE_FUNNEL_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/PIPELINE_FUNNEL_REPORT.md), there is a discrepancy between the total ingested count and the sum of processed states:
*   **Total Ingested**: 4260
*   **Low-Context Bypass**: 750
*   **Total Classified**: 3338
*   *Discrepancy*: $4260 - 750 - 3338 = 172$ items are unaccounted for in the summary metrics.

### Diagnostic Analysis
A query of `source_item` and `source_item_text` table states for the cohort lookback window reveals the exact composition of the 172 unaccounted items:
1.  **1 item**: Text processing failed (`text_processing_status = 'failed'`). Under pipeline lifecycle rules in [DATA_LIFECYCLE.md](file:///C:/Users/user/Documents/exopolitics/docs/DATA_LIFECYCLE.md), failed items terminate at ingest and do not generate a downstream row in `classification_result`.
2.  **171 items**: Text processing completed successfully (`text_processing_status = 'completed'`) but no classification result has been written yet.
    *   *Root Cause*: These 171 items were fetched between `2026-07-14T15:01:43Z` and `2026-07-15T01:01:39Z`, which is *after* the latest classification batch run (max `fetched_at` of classified items is `2026-07-14T10:01:39Z`). They are pending in the queue for the next classification batch run.

### Recommendations for Engineers
*   **Self-Reconciling Metrics**: Update the JSON schema in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) and the reporting services to explicitly expose `pending_classification` and `failed_ingest` counts.
*   **Maturation Delay Offset**: Implement a query configuration parameter (e.g. maturation lookback offset of 2-6 hours) to filter out very recently ingested items from the conversion rate denominator, avoiding artificial skew of classification conversion rates.

---

## 2. Pipeline Latency Analysis & Initialization Skew

### Observation
In [PIPELINE_FUNNEL_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/PIPELINE_FUNNEL_REPORT.md), the stage latencies show extremely high values:
*   **E2E Pipeline Lead Time (p50)**: 112562.00s (~31 hours)
*   **Feed Freshness Delay (p90)**: 105368279.00s (~3.3 years)
*   **Classification Delay (p50)**: 81715.50s (~22.7 hours)

### Diagnostic Analysis
A diagnostic query of the canonical database reveals that the numbers are mathematically correct but heavily skewed by the **initialization batch**:
1.  **Massive Batch Ingestion**: On `2026-07-12 06:00:00`, a single batch of **3,689 items** was ingested during database initialization. This accounts for **86.6%** of the entire 7-day lookback window cohort (4,260 items).
2.  **Historical Ingestion (Freshness Delay)**: Because historical articles (published months/years ago) were fetched for the first time, `fetched_at - published_at` naturally resulted in extreme delays (up to 3.3 years at p90).
3.  **Processing Latency Skew**: The initialization batch sat in the database for 22 to 24 hours before the classification batch runner was executed on it (accounting for 3,130 items in the 22-24h delay bucket). Because these items dominate the cohort, they pull the median (p50) classification delay up to 22.7 hours.
4.  **Steady State Performance (Small Batches <= 50 items)**: 
    For normal hourly updates, the pipeline is extremely fast and healthy:
    *   **Average E2E Lead Time**: **30.75s**
    *   **Average Classification Delay**: **6.05s**
    *   **Average Curation Delay**: **9.65s**
    *   **Average Translation Delay**: **10.50s**
    *   **Average Publish Delay**: **4.75s**

### Recommendations for Engineers

#### 1. Temporal Self-Correction Note
For standard 7-day reports, the initialization skew is a temporary issue that will **naturally self-correct** once the July 12th initialization batch falls outside the 7-day lookback window (i.e., from July 19th onwards). However, design adjustments are still recommended to handle long-term reporting windows and future source additions:

#### 2. Long-Term Actions
*   **Prevent Skews from Future Source Additions**: Whenever a new RSS feed is added to `sources.yaml` in the future, the initial fetch will import historical entries, causing local spikes in `Feed Freshness Delay`. To prevent this, implement a rule to **exclude the first fetch run of any new source** from freshness latency metrics.
*   **Long-Lookback Windows**: For 30-day or 90-day reports, the initialization batch will pollute the stats for a long time. Consider introducing a `--exclude-initialization` flag or filtering out runs where `fetch_run.run_scope = 'catchup'` or similar metadata.
*   **Log-Transform or Trim Outliers**: Provide trimmed mean/median statistics or filter out extreme historical records (e.g., items where `fetched_at - published_at > 30 days`) when evaluating feed harvesting freshness to prevent metric pollution.

---

## 3. Lack of Core vs. Adjacent Topic Class Breakdown in Reports

### Observation
In [CLASSIFY_MONITOR_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/CLASSIFY_MONITOR_REPORT.md), the classification performance breakdown only evaluates a unified `Relevance Rate` (representing items classified as either `core` or `adjacent` divided by total items). It does not differentiate between the proportions of `core` (highly relevant, e.g. direct UAP sightings) and `adjacent` (indirectly relevant, e.g. general space/aerospace news) topic classes.

Similarly, in [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md), the Markdown table only lists `Relevance` as a unified column. Although the underlying JSON schema of the `sources` report calculates `topic_class_breakdown` (with `core`, `adjacent`, `irrelevant`, and `unknown` rates), this breakdown is **not formatted or printed** in the human-readable Markdown output.

### Diagnostic Analysis & Spec Alignment (Implementation Gap)
Reviewing the original technical specifications reveals that **this is an Implementation Gap rather than a Design Flaw**. The architecture and specs planned this correctly, but the code failed to reflect it:
*   **Design was Correct**: [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md) Section 2.3.1 explicitly defines `Topic Class Breakdown [MVP]` to *"preserve the full distribution... instead of flattening all relevant outcomes into a single scalar"* and states it is a **required output field**. The `sources` JSON schema in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) also requires this breakdown.
*   **Implementation Omitted It**: 
    *   *Backend Query Omission*: The SQL in `classify_queries.py` completely flattens the result using `cr.topic_class IN ('core', 'adjacent')` and lacks the individual breakdowns, deviating from the spec.
    *   *Markdown Rendering Omission*: Even though the `sources` backend service query calculates these ratios (`prop_core`, `prop_adjacent`), the Markdown formatter in `SourceService.format_markdown_report` flattens them into a single `Relevance` column in [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md).
    *   This creates an **information transparency gap** because operators reviewing the human-readable Markdown reports cannot distinguish whether a source yields primary core UAP content or general aerospace news.

### Recommendations for Engineers
*   **Fix Backend/Service Logic Deviation**: In `classify_queries.py` and `classify_service.py`, calculate and expose the individual rates for `core` and `adjacent` topic classes in both the JSON schema and the generated [CLASSIFY_MONITOR_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/CLASSIFY_MONITOR_REPORT.md) Markdown table, bringing it in line with the design spirit of [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md).
*   **Fix Markdown Rendering Omission**: Update [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md) generator to display the `core` and `adjacent` proportion breakdown in the Markdown table (e.g., as `Relevance (Core/Adj)` column like `90% (30%/60%)`), rather than hiding it strictly in the JSON payload.


---


## 4. Curation Workload Metrics (Character Volume Proxy) Exposure Gap

### Observation
While LLM workload character proxies are successfully exposed in [CLASSIFY_MONITOR_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/CLASSIFY_MONITOR_REPORT.md) and [TRANSLATION_PERFORMANCE_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/TRANSLATION_PERFORMANCE_REPORT.md), the **Curation Workload** (representing the number of characters evaluated by the Curation LLM) is not visible in any generated Markdown reports.

### Diagnostic Analysis
A code audit reveals that the underlying query logic and service calculations are fully implemented but omitted from final presentation:
1.  **Curation Service exists but is unregistered**: [CurateService](file:///C:/Users/user/Documents/exopolitics/modules/analysis/src/services/curate_service.py) is fully written and correctly calculates `curation_character_volume_proxy`. However, no `analyze-curation` subcommand is registered in [cli.py](file:///C:/Users/user/Documents/exopolitics/modules/analysis/src/cli.py), meaning no standalone `CURATION_PERFORMANCE_REPORT.md` is generated.
2.  **Sources Report contains the data but hides it**: The SQL in `aggregation_queries.py` correctly queries `curation_character_volume_proxy` as `curate_char_vol`, and `SourceService` returns it in its JSON breakdown. However, `SourceService.format_markdown_report` flattens the output and does not print this column in [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md).
3.  This represents an **Implementation Gap** that hides curation cost/workload proxies from operators.

### Recommendations for Engineers
*   **Update Sources Markdown Table**: Expose both `Classification Char Volume` and `Curation Char Volume` as columns in the [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md) Markdown table to provide a comprehensive look at source processing overhead.
*   **Or Register Curation CLI Command**: Register `analyze-curation` subcommand in [cli.py](file:///C:/Users/user/Documents/exopolitics/modules/analysis/src/cli.py) to allow operators to run curation diagnostics and output `CURATION_PERFORMANCE_REPORT.md`.

---

## 5. Reserved Space for Upcoming Issues / Questions

This section is reserved for documenting further questions or findings raised during system reviews.

### [Issue Title Placeholder]
*   **Status**: Pending review
*   **Description**: *[To be completed based on subsequent feedback]*
*   **Impact**: *[To be completed]*
*   **Action Items**: *[To be completed]*

