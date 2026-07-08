# Analysis Spec Revision Proposal

This document summarizes the findings and recommended specifications revisions for the `analysis` module. These proposals address inconsistencies between the draft specs and the actual database schema in [canonical.db](file:///C:/Users/user/Documents/exopolitics/data/canonical.db), and resolve functional gaps in multi-language publication tracking.

---

## 1. Issue: Column Naming Inconsistency (`publish_record.published_at`)

### 1.1 Finding
In both [MVP_METRICS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/MVP_METRICS.md) (Metric 9: Publish Count) and [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md#L122) (Section 2.7: Publish Count), the SQL filter formula is specified as:
```text
where publish_record.published_at is within the lookback window
```

However, the actual table schema for `publish_record` in the database does not contain a `published_at` column:
*   **Actual `publish_record` Columns**: `publish_record_id`, `source_item_id`, `slug`, `first_published_at`, `created_at`, `updated_at`.
*   **Risk**: Writing SQL queries using `publish_record.published_at` will crash at runtime with a `no such column` database error.

### 1.2 Recommended Fixes
*   **Mother-Draft Metric (Global Count)**: Change the query field to `publish_record.first_published_at`.
*   **Updates to documentation**:
    1.  In [MVP_METRICS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/MVP_METRICS.md#L94), change the formula definition to count records where `publish_record.first_published_at` falls in the window.
    2.  In [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md#L122), change the formula and notes to reference `publish_record.first_published_at`.

---

## 2. Issue: Lack of Multi-Language Publication Output Schema

### 2.1 Finding
Although the translation execution rate is tracked in the draft specs, there is currently no structured metric or JSON schema contract for tracking **actual successful publication count by language** (e.g. 2000 English, 1995 Chinese, 1998 Japanese). 
*   **Database Support**: The database already has a table named `publish_language_status` which tracks `language_code`, `publish_status`, and `published_at` for each translation, allowing this to be resolved natively.
*   **Draft Schema Omission**: In [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md), the `funnel` and `translation` report schemas only include a flat global integer `total_published` and translation completion rates, leaving a gap for displaying language-specific counts on UI dashboards.

### 2.2 Recommended Fixes
*   **Add `published_by_language` Breakdown**: Update the output contract in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) to define a structured breakdown grouped by `language_code`.
*   **JSON Schema Addition**:
    In the `funnel` or `translation` report breakdown array, add a schema mapping:
    ```json
    "published_by_language": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "language_code": { "type": "string" },
          "published_count": { "type": "integer" },
          "coverage_rate": { "type": "number" }
        },
        "required": ["language_code", "published_count", "coverage_rate"]
      }
    }
    ```
*   **SQL Formula**:
    ```sql
    SELECT language_code, 
           COUNT(*) AS published_count,
           COUNT(*) * 1.0 / (SELECT COUNT(*) FROM publish_record WHERE first_published_at BETWEEN :start AND :end) AS coverage_rate
    FROM publish_language_status
    WHERE publish_status = 'published'
      AND published_at BETWEEN :start AND :end
    GROUP BY language_code;
    ```

---

## 3. Issue: Underestimation Risk in Translation Character Volume Proxy

### 3.1 Finding
In [MVP_METRICS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/MVP_METRICS.md) (Metric 10.C: Translation Character Volume Proxy) and [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md#L160) (Section 3.1.3), the workload is estimated by summing approved content length where the item **has a row in `translation_output`**.

While the translation orchestrator codebase ([orchestrator.py:L481](file:///C:/Users/user/Documents/exopolitics/modules/translate/src/orchestrator.py#L481)) does attempt to write a row with `translation_status = 'failed'` upon API failure, there is a significant risk of underestimating token costs:
*   **Catastrophic Failures**: If the system crashes mid-execution, hits a database lock, or terminates before the `except` block can successfully write to `translation_output`, no DB record is generated. However, the LLM API call was already made and cost money.
*   **Unrecorded API Calls**: Basing cost proxy metrics strictly on database-recorded outputs ignores API charges incurred during hard failures or memory crashes.

### 3.2 Recommended Fixes
*   **Alternative 1: Curation-Based Intended Workload (Upper Bound)**:
    Estimate the cost by multiplying the character length of all approved content in `approved_content_record` by the number of active target languages (excluding self-translation bypass). This represents the **Total Intended Translation Workload** and provides a safer budget upper-bound:
    ```sql
    -- Theoretical maximum workload assuming all approved items are sent for translation
    SELECT SUM(length(display_title) + length(content_body)) * :target_lang_count
    FROM approved_content_record
    WHERE approved_at BETWEEN :start AND :end;
    ```
*   **Alternative 2: Document Warning & Dual Metric**:
    Keep the database join but explicitly document that this metric represents *recorded* workload and might under-report during network or execution crashes.

---

## 4. Issue: Redundant Metric Elimination (Filtering Overhead vs. Overall Yield)

### 4.1 Finding
In [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md), two metrics are defined that are mathematically redundant (reciprocals of each other):
1.  **Overall Yield** (Section 2.5):
    $$\text{Overall Yield} = \frac{\text{Curate Approved Count}}{\text{Total Ingested (Classified)}}$$
2.  **Classification Filtering Overhead** (Section 3.2):
    $$\text{Classification Filtering Overhead} = \frac{\text{Total Classified}}{\text{Curate Approved}}$$

Since the number of total classified items is extremely close to the total ingested items (excluding thin low-context bypasses), **Filtering Overhead is simply the reciprocal of Overall Yield**:
*   If a source has an **Overall Yield of 10% (0.10)**, its **Filtering Overhead is $1 / 0.10 = 10.0$** (meaning we must process 10 raw articles to get 1 approved).
*   If a source has an **Overall Yield of 50% (0.50)**, its **Filtering Overhead is $1 / 0.50 = 2.0$**.
*   **Redundancy**: Having both metrics on the dashboard and in the JSON schema increases code complexity and cognitive load for operators without providing new information.

### 4.2 Recommended Fix
*   **Eliminate Classification Filtering Overhead**: Drop this metric entirely from [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md) and the JSON output schemas in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md).
*   **Focus on Overall Yield**: Keep `Overall Yield` as the primary metric, as percentages (e.g., "10% Yield") are generally more intuitive for users than multipliers (e.g., "10.0x Overhead").

---

## 5. Issue: Redundancy in Unique Contribution Rate under URL-Only Dedup

### 5.1 Finding
In [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md) (Section 3.5), `Unique Contribution Rate` is defined as:
$$\text{Unique Contribution Rate} = \frac{\text{Curate Approved (Undeduped)}}{\text{Total Ingested}}$$

However, as established in the `ingest` module's architecture ([DATA_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/modules/ingest/docs/DATA_CONTRACT.md#L155-L158)), the system **does not perform complex cross-source content similarity deduplication**. It only supports **exact URL-based cross-source deduplication**. All other rules (`guid`, `title+date`, `hash`) are source-scoped (intra-source).

Under this design:
*   If Blog A copies Blog B's content but publishes it under a different URL (which is standard practice), the system **cannot detect it** as a cross-source duplicate.
*   Cross-source duplicates will only be flagged if two feeds point to the *exact same canonical URL* (which is rare except for link-aggregators).
*   **Result**: The number of cross-source duplicates detected will be extremely close to zero. Consequently, for 99% of sources, the `Unique Contribution Rate` will be **virtually identical to the `Overall Yield`** ($\frac{\text{Curate Approved}}{\text{Total Ingested}}$).

### 5.2 Recommended Action: Database-Backed Validation
To ensure we do not make hasty design decisions, we recommend that engineers first execute a validation query on the live [canonical.db](file:///C:/Users/user/Documents/exopolitics/data/canonical.db) to evaluate the actual proportion of duplicate matches being caught by the current ingest engine:

1.  **Run the Validation Query**:
    ```sql
    -- Query the total new items vs. duplicates caught across all fetch runs
    SELECT 
        SUM(new_item_count) AS total_new_items,
        SUM(dedup_matched_count) AS total_duplicates_caught,
        ROUND(SUM(dedup_matched_count) * 100.0 / (SUM(new_item_count) + SUM(dedup_matched_count)), 2) AS duplicate_ratio_percentage
    FROM fetch_attempt;
    ```
2.  **Evaluate the Results**:
    *   **If the Duplicate Ratio is Negligible (< 1% ~ 2%)**: This confirms that the current URL-only deduplication yields very few cross-source matches. We should **remove the Unique Contribution Rate** to avoid code bloat.
    *   **If the Duplicate Ratio is Significant (> 5%)**: This indicates that URL-based cross-source duplicates are frequent enough to warrant monitoring. We should **keep the metric** and proceed with implementing it.

---

## 6. Issue: Multi-Language Skew in Translation Delay Metric

### 6.1 Finding
In [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md) (Section 4.2.5), `Translation Delay` is defined as:
```text
translation_output.translated_at - approved_content_record.approved_at
```

Evaluating translation speed under a multi-language pipeline introduces two significant sources of statistical skew:
1.  **Self-Translation Bypass (0-Second Bias)**: If an article is written in English, and the target language is also English, the orchestrator triggers a "self-translation bypass" ([orchestrator.py:L386](file:///C:/Users/user/Documents/exopolitics/modules/translate/src/orchestrator.py#L386)) which writes to `translation_output` instantly (0 seconds delay). Mixing these bypass records with actual LLM translations will artificially drag down the global average/median delay, making the API pipeline look faster than it actually is.
2.  **Language-Specific Variance**: Different target languages (e.g., Chinese, Japanese, Spanish) are processed as separate rows in `translation_output` and may experience different API rate limits, model selections, or retry counts. Aggregating them into a single global average (`translation_delay` in the funnel report) hides language-specific performance bottlenecks.

### 6.2 Recommended Fixes
*   **Exclude Self-Bypasses**: Update the SQL formula to exclude rows where `target_language = source_language` (e.g. `translation_output.model_name = 'bypass'`) from the latency calculations.
*   **Report Per-Locale Latency**: Ensure the global funnel report only tracks the latency of actual translations, while the detailed translation report provides target-language breakdowns (e.g. median delay for `zh` vs `ja`) to isolate translation bottlenecks.

---

## 7. Issue: Redundancy and Simplification in Translation Section

### 7.1 Finding 1: Identical Latency Metrics (`4.2.5` vs. `4.3.5`)
In [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md), two identical metrics are defined:
*   `4.2.5 Translation Delay` (under Stage Latency)
*   `4.3.5 Translation Latency / Delay` (under Translation Performance)

Both metrics use the exact same calculation formula: `translation_output.translated_at - approved_content_record.approved_at`. The only difference is the reporting context (the Funnel Report vs. the Translation Sub-report). Listing them as two distinct metrics creates confusion and redundant documentation.

### 7.2 Finding 2: Over-engineering of `Stale Rate` in MVP (`4.3.3`)
`Translation Stale Rate` tracks the percentage of translations canceled because the original article was updated or marked as stale.
*   **Redundancy**: A stale translation is already captured as a non-successful event in the denominator of `Translation Success Rate` ($\frac{\text{Completed}}{\text{Completed} + \text{Failed} + \text{Stale}}$).
*   **Complexity**: Creating a separate dashboard KPI for this niche edge case is unnecessary for Phase 1. If an item becomes stale, it is functionally a failed/discarded attempt and can be debugged via logs rather than tracked as a top-level KPI.

### 7.3 Finding 3: Redundancy in `Translation Completion Rate` (`4.3.2`) under Serial Execution
`Translation Completion Rate` tracks how many approved articles in `approved_content_record` successfully completed translation.
*   **Redundancy**: If the translation pipeline is synchronous/serial (run-to-completion batch execution without parallel concurrent queue states), every approved item is immediately attempted during a run. At the end of the run, an item is either successfully translated (`completed`) or failed (`failed`).
*   **Mathematical Equivalence**: In this serial setup, there is no permanent "pending queue backlog" spanning runs. Thus, `Translation Completion Rate` ($\frac{\text{Completed}}{\text{Total Approved}}$) becomes mathematically equivalent to `Translation Success Rate` ($\frac{\text{Completed}}{\text{Total Attempted}}$).
*   **Exceptions**: The only time they differ is if we enforce a strict `batch_size` limit (e.g. only translating 50 out of 100 approved items, leaving 50 in queue). If no batch limits are enforced, the metric is entirely redundant.

### 7.4 Recommended Fixes
*   **Consolidate Latency**: Merge `4.2.5` and `4.3.5` into a single, unified metric `Translation Delay / Latency` in the catalog. Clarify in the notes that it can be aggregated either globally (funnel) or grouped by locale (translation sub-report).
*   **Deprecate Stale Rate**: Remove `Translation Stale Rate` (`4.3.3`) from the MVP metrics catalog. Keep `stale` as a sub-status in database logs, but do not expose it as a standalone dashboard metric to keep the report simple.
*   **Deprecate Completion Rate for MVP**: Unless the pipeline introduces parallel concurrent queues or strict batch-size limits that backlog items across runs, remove `Translation Completion Rate` (`4.3.2`) from the MVP scope to reduce code complexity.

---

## 8. Issue: Decoupling CLI Defaults into Configuration Files

### 8.1 Finding
In [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) (Section 1.1: Unified CLI Options), default values for CLI parameters (such as `--days 7`, `--format markdown`, `--output-dir reports/analysis/`) are specified. 

If these defaults are hardcoded directly into the Python source code (e.g., using `click.option(default=...)`), it creates several maintenance challenges:
*   **Operational Rigidity**: Changing the default reporting directory or lookback window in a production environment requires code modification rather than simple configuration updates.
*   **Cron Job Verbosity**: If operators want to run different defaults, they must write highly verbose shell scripts with multiple CLI flags rather than relying on a clean config file.

### 8.2 Recommended Action: Three-Level Precedence Chain
To maximize flexibility while maintaining safety, we should implement a hierarchical configuration resolution chain for CLI defaults:

1.  **Level 1 (Highest Precedence)**: Explicit CLI Flags passed at runtime (e.g. `--days 14` overrides all).
2.  **Level 2 (Medium Precedence)**: Configuration file settings loaded from the module-local config file `modules/analysis/config/analysis_settings.yaml`.
3.  **Level 3 (Lowest Precedence / Fallback)**: Hardcoded code-level defaults (fallback safety values in Python).

### 8.3 Recommended Config Schema Expansion
We suggest using `analysis_settings.yaml` as the unified module-local settings file and expanding it to store these reporting defaults:
```yaml
schema_version: 1

reporting:
  defaults:
    days: 7
    format: "markdown"
    output_dir: "reports/analysis/"
    stdout: false

quadrant_classifier:
  thresholds:
    overall_yield: 0.10
    relevance_rate: 0.40
  safeguards:
    fetch_success_rate_isolation: 0.50
```

---

## 9. Issue: Granular Topic Class Breakdown by Source

### 9.1 Finding
In [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md), `Relevance Rate` is represented as a single aggregate number (e.g. 950 relevant items). 

However, since `classification_result.topic_class` already stores four distinct states (`core`, `adjacent`, `unknown`, `irrelevant`), flattening this data hides critical qualitative differences between sources:
*   **High-Value vs. Low-Value Sources**: Two sources might both have an 80% Relevance Rate, but Source A yields 80% `core` content (official disclosures) while Source B yields 80% `adjacent` content (general community discussion). Currently, they look identical.
*   **Identification of Dead Weight**: Sources that consistently return `irrelevant` (unrelated) or `unknown` (low-quality or garbage content) are primary candidates for disabling to save LLM token costs.

### 9.2 Recommended Fix
*   **Add Category Breakdown to CLI and JSON**:
    Update `analyze-sources` to output a breakdown of `topic_class` percentages for each source.
*   **JSON Schema Update**:
    In [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md), add a nested structure to the source breakdown schema:
    ```json
    "topic_class_breakdown": {
      "type": "object",
      "properties": {
        "core": { "type": "number", "description": "Percentage of core items" },
        "adjacent": { "type": "number", "description": "Percentage of adjacent items" },
        "irrelevant": { "type": "number", "description": "Percentage of irrelevant items" },
        "unknown": { "type": "number", "description": "Percentage of unknown/unmapped items" }
      },
      "required": ["core", "adjacent", "irrelevant", "unknown"]
    }
    ```
*   **SQL Query Pattern**:
    ```sql
    SELECT si.source_id, cr.topic_class, COUNT(*) AS count
    FROM classification_result cr
    JOIN source_item si ON cr.source_item_id = si.source_item_id
    WHERE si.fetched_at BETWEEN :start AND :end
    GROUP BY si.source_id, cr.topic_class;
    ```

---

## 10. Strategic Proposal: Defer DECISION_MODELS.md to Phase 2

### 10.1 Finding & Rationale
[DECISION_MODELS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DECISION_MODELS.md) defines high-level recommendation heuristics (such as the 4-quadrant source classifier, authority tagging, and connection diagnostics isolation lists). 

Developing these complex decision models in Phase 1 MVP introduces substantial risk and potential waste:
1.  **Unstable Baselines**: We do not yet know the true statistical distribution of our RSS data (e.g. what is a realistic yield threshold? Is 10% too high or too low?). Coding decision logic based on speculative baselines (like the current `0.10` overall yield default) will lead to false recommendations.
2.  **Double Work on Schema Changes**: Since Phase 1 metrics are still being adjusted and pruned (e.g. deprecating unique contribution rate, consolidating delay), any change to metrics immediately breaks the decision logic built on top of them.
3.  **Low Initial Automation Need**: In the early stage of the website, the number of active sources is small (~80 feeds). A human operator can easily look at the raw metrics tables (e.g. noting a source has 95% irrelevant content) and make manual decisions, without needing an automated heuristic classifier.

### 10.2 Recommended Action
*   **Defer Development**: Postpone the implementation of [DECISION_MODELS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DECISION_MODELS.md) to Phase 2 (or a "Stabilization Phase").
*   **Phase 1 Focus**: Focus strictly on extracting and displaying clean, accurate raw metrics (success rates, volume, topic class breakdown, latency).
*   **Data Gathering Period**: Run the raw metrics pipeline for 2–4 weeks. Use the gathered baseline data to calibrate the thresholds (yield, relevance, fetch success) before coding the automated classification rules in Phase 2.

---

## 11. Additional Suggestion: CLI Safeguards Override Parameter

### 11.1 Finding
In [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md), the CLI subcommand `analyze-sources` supports overriding the Yield threshold (`--yield-threshold`) and Relevance threshold (`--relevance-threshold`) but lacks a parameter to override the connection diagnostics isolation threshold.

### 11.2 Recommended Fix
Add a CLI override option to complement the others:
*   `--fetch-isolation-threshold FLOAT`: Optional override for the `fetch_success_rate_isolation` parameter (default: loads from `analysis_settings.yaml`).








