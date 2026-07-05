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

### 2.2 Fetch Health Isolation
*   **Rule**: Feeds with a $\text{Fetch Success Rate} < 50\%$ are isolated.
*   **Action**: Isolated feeds are put on the "Connection Diagnostics List" and excluded from content-quality analysis. This prevents transient connection issues (like anti-bot blocks or temporary server outages) from skewing their historical quality/yield metrics.
