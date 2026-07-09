# Decision Models

This document defines the interpretation heuristics applied to stable metrics to support human source-auditing decisions.

> [!NOTE]
> The `analysis` module only *recommends* decisions. The responsibility of applying actions (such as disabling a source in `sources.yaml`) remains with the respective operational modules.

> [!IMPORTANT]
> Phase 1 should treat these rules as a reporting-layer aid only. The module may emit derived labels, quadrant classifications, and analysis flags in Markdown or JSON output, but it must not perform automated source-disable actions or mutate operational configuration.

---

## 1. Source Quadrant Classifier

The quadrant model is a lightweight interpretation layer over raw metrics. It is intended to help operators review source quality quickly in text reports or structured JSON, even when no chart-based UI is present.

Sources with a $\text{Fetch Success Rate} \ge 50\%$ are categorized into one of four quadrants based on their **Overall Yield** (vertical axis) and a horizontal-axis relevance/uniqueness signal that varies by phase:

```text
       High |
            |   Needle in a Haystack              |   Golden Source
            |   - Characteristics: Low overall    |   - Characteristics: High yield (>60%),
            |     yield but high authority value  |     low filtering overhead.
Yield       |   - Strategy: Keep visible for      |   - Strategy: Recommend keeping and
(Yield)     |     manual periodic reviews.        |     consider higher fetch frequency.
            |-------------------------------------+--------------------------------------
            |   Dead Weight                       |   Filtering Burden
            |   - Characteristics: Extremely low  |   - Characteristics: High volume, high
            |     yield, high duplicates/         |     noise/processing load.
            |     low-context                     |   - Strategy: Tighten keywords,
            |   - Strategy: Recommend disable     |     pre-filtering, or reduce cadence.
        Low |     after operator review           |______________________________________
            Low                                                                          High
                                      Relevance Rate / Uniqueness
```

### 1.1 Horizontal Axis Calibration
To ensure predictable classification as the pipeline matures:
*   **Phase 1 (MVP)**: The horizontal axis uses `Relevance Rate` as the scalar threshold axis (since unique deduplication metrics are deferred to Phase 2). `topic_class_breakdown` must still be emitted alongside it to preserve the distinction between `core`, `adjacent`, `irrelevant`, and `unknown` source behavior during operator review.
*   **Phase 2**: Once deduplication markers are fully integrated into the pipeline, the horizontal axis may be replaced or augmented by `Unique Contribution Rate`.

### 1.2 Quadrant Strategies
*   **Golden Source**: Recommend maintaining these high-yield, low-overhead feeds and consider higher fetch frequency.
*   **Filtering Burden**: Recommend reducing crawl cadence, tightening keyword filters, or implementing upstream pre-filtering to reduce token/workload footprint.
*   **Needle in a Haystack**: Keep visible for manual review; these sources may retain strategic value despite low yield.
*   **Dead Weight**: Mark as an audit or disable candidate for operator review.

### 1.3 Phase 1 Output Scope
For Phase 1, the implementation may expose this model only as derived reporting fields such as:
*   `quadrant`
*   `analysis_flags`

The `analysis_flags` list may include values such as `AUTHORITY`, `CONNECTION_DIAGNOSTICS`, and `INSUFFICIENT_DATA`. These fields may be rendered in Markdown tables, plain numeric summaries, or JSON output. No chart-based UI is required for this model to be useful.

---

## 2. Safeguards & Isolations

In report contracts, the outcomes of these rules should be represented as `analysis_flags`. They are analysis-time context markers, not error messages and not automated actions.

### 2.1 Authority Tagging (Visual Indicator Only)
*   **Rule**: Feeds with `category_id: 1` (Government & Official Disclosures) and `category_id: 3` (Scientific Validation & Research) are tagged with `[AUTHORITY]`.
*   **Action**: These feeds are **not exempt** from quadrant classification or recommendations. Their yield and relevance are presented raw and faithfully. The `[AUTHORITY]` tag serves purely as a visual indicator in reports to assist human operators in manual screening.
*   **Metadata Resolution**: Since the database `canonical.db` does not contain `category_id` or source category mappings, the analysis module must read the external [sources.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/sources.yaml) config file to lookup the `category_id` corresponding to the item's `source_id` before evaluating this rule. See [DATA_DEPENDENCIES.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DATA_DEPENDENCIES.md) for join constraints.

### 2.2 Fetch Health Isolation
*   **Rule**: Feeds with a $\text{Fetch Success Rate} < 50\%$ are isolated.
*   **Action**: Isolated feeds are placed on the "Connection Diagnostics List" and excluded from content-quality analysis for the current run. This prevents transient connection issues (like anti-bot blocks or temporary server outages) from skewing their historical quality/yield metrics.

### 2.3 Operational Thresholds & Null-Handling
To support flexibility in pipeline tuning, thresholds are designed as configurable parameters rather than hardcoded constants. The implementation must load these parameters from the module-local configuration file [analysis_settings.yaml](file:///C:/Users/user/Documents/exopolitics/modules/analysis/config/analysis_settings.yaml) during runtime, with the CLI optionally supporting override flags (e.g. `--yield-threshold` and `--relevance-threshold`). This allows operators to dynamically adjust sensitivity after analyzing database statistics.

#### 2.3.1 Config File Schema & Defaults
The file `modules/analysis/config/analysis_settings.yaml` contains the baseline configuration:

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
    # Baseline defaults (subject to adjustments based on database distribution analysis)
    overall_yield: 0.10          # High Yield >= 10%, Low Yield < 10%
    relevance_rate: 0.40         # High Relevance >= 40%, Low Relevance < 40%
  safeguards:
    fetch_success_rate_isolation: 0.50  # Fetch Success Rate < 50% triggers isolation
```

#### 2.3.2 Null and Zero Ingestion Handling
*   **Zero Ingestion**: If a source has `Ingest Volume = 0` during the lookback window, its Relevance Rate and Overall Yield are mathematically undefined (`NULL`).
*   **Action**: These sources are excluded from the Source Quadrant Classifier and tagged as `[INSUFFICIENT_DATA]`. They may still be surfaced for manual review, but must not trigger automated disable recommendations.

#### 2.3.3 Tie-Breaks and Precedence
1.  **Fetch Isolation Priority**: If a source has a $\text{Fetch Success Rate} < 50\%$ during the current lookback window, it is isolated and placed on the "Connection Diagnostics List". Its content quality classification (Quadrant) is suspended for the current run, regardless of its content yield. This prevents temporary connection issues from generating misleading content quality classifications.
2.  **Insufficient Data Precedence**: If a source has $\ge 50\%$ fetch success rate but has `Ingest Volume = 0`, it must be classified as `[INSUFFICIENT_DATA]`. Curation or translation quality models must not run on this source.
