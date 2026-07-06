# Decision Models

This document defines the decision-making logic and heuristics applied to stable metrics to generate optimization recommendations. 

> [!NOTE]
> The `analysis` module only *recommends* decisions. The responsibility of applying actions (such as disabling a source in `sources.yaml`) remains with the respective operational modules.

---

## 1. Source Quadrant Classifier

Sources with a $\text{Fetch Success Rate} \ge 50\%$ are categorized into one of four quadrants based on their **Overall Yield** (vertical axis) and a horizontal-axis relevance/uniqueness signal that varies by phase:

```text
       High |
            |   Needle in a Haystack              |   Golden Source
            |   - Characteristics: Low overall    |   - Characteristics: High yield (>60%),
            |     yield but high authority value  |     low filtering overhead.
Yield       |   - Strategy: Protect in list,      |   - Strategy: Keep and increase
(Yield)     |     manual periodic reviews.        |     fetch frequency.
            |-------------------------------------+--------------------------------------
            |   Dead Weight                       |   Filtering Burden
            |   - Characteristics: Extremely low  |   - Characteristics: High volume, high
            |     yield, high duplicates/         |     noise/processing load.
            |     low-context                     |   - Strategy: Tighten keywords,
            |   - Strategy: Disable in            |     pre-filtering, or reduce cadence.
        Low |     sources.yaml                    |______________________________________
            Low                                                                          High
                                      Relevance Rate / Uniqueness
```

### 1.1 Horizontal Axis Calibration
To ensure predictable classification as the pipeline matures:
*   **Phase 1 (MVP)**: The horizontal axis relies strictly on `Relevance Rate` (since unique deduplication metrics are deferred to Phase 2).
*   **Phase 2**: Once deduplication markers are fully integrated into the pipeline, the horizontal axis may be replaced or augmented by `Unique Contribution Rate`.

### 1.2 Quadrant Strategies
*   **Golden Source**: Maintain high fetch frequency. These are high-yield, low-overhead feeds.
*   **Filtering Burden**: Reduce crawl cadence, tighten keyword filters, or implement upstream pre-filtering to reduce token/workload footprint.
*   **Needle in a Haystack**: Protect in the active source list; perform manual reviews periodically.
*   **Dead Weight**: Recommend disabling in `sources.yaml`.

---

## 2. Safeguards & Isolations

### 2.1 Authority Protection Mechanism
*   **Rule**: Feeds with `category_id: 1` (Government & Official Disclosures) and `category_id: 3` (Scientific Validation & Research) are tagged with `[AUTHORITY]`.
*   **Action**: These feeds are **exempt** from automated disable/quarantine recommendations. This protects low-frequency but critical high-integrity sources from being discarded.
*   **Metadata Resolution**: Since the database `canonical.db` does not contain `category_id` or source category mappings, the analysis module must read the external [sources.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/sources.yaml) config file to lookup the `category_id` corresponding to the item's `source_id` before evaluating this rule.

### 2.2 Fetch Health Isolation
*   **Rule**: Feeds with a $\text{Fetch Success Rate} < 50\%$ are isolated.
*   **Action**: Isolated feeds are put on the "Connection Diagnostics List" and excluded from content-quality analysis. This prevents transient connection issues (like anti-bot blocks or temporary server outages) from skewing their historical quality/yield metrics.

### 2.3 Operational Thresholds & Null-Handling
To support flexibility in pipeline tuning, thresholds are designed as configurable parameters rather than hardcoded constants. The implementation must load these parameters from the module-local configuration file [decision_rules.yaml](file:///C:/Users/user/Documents/exopolitics/modules/analysis/config/decision_rules.yaml) during runtime, with the CLI optionally supporting override flags (e.g. `--yield-threshold` and `--relevance-threshold`). This allows operators to dynamically adjust sensitivity after analyzing database statistics.

#### 2.3.1 Config File Schema & Defaults
The file `modules/analysis/config/decision_rules.yaml` contains the baseline configuration:

```yaml
schema_version: 1

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
*   **Action**: These sources are excluded from the Source Quadrant Classifier and tagged as `[INSUFFICIENT_DATA]`. They must not trigger automated disable recommendations.

#### 2.3.3 Tie-Breaks and Precedence
1.  **Fetch Isolation Priority**: If a source has a $\text{Fetch Success Rate} < 50\%$ but has historically high yield, the **Fetch Health Isolation** rule takes precedence. The source is placed in the connection diagnostics list, and its quality classification is suspended until connectivity is stable.
2.  **Authority Exemption Priority**: If a source is classified as "Dead Weight" (low yield, low relevance) but carries the `[AUTHORITY]` flag, the **Authority Protection** rule takes precedence. The recommendation is overridden to "Maintain Source (Protected)" instead of "Disable".
