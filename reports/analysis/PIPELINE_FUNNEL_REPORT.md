# Pipeline Funnel Conversion & Bottleneck Report

**Generated At**: 2026-07-23T18:24:51Z
**Lookback Period**: 7 days
**Maturation Delay Offset**: 2 hours

## 1. Raw Window Cohort Performance
**Raw Window**: 2026-07-16T18:24:51Z to 2026-07-23T18:24:51Z

### Throughput Metrics
- **Total Ingested**: 1231
- **Low-Context Observation Count**: 330
- **Total Classified**: 901
- **Relevant Classified**: 316
- **Total Curated**: 316
- **Curation Approved**: 262
- **Total Translated**: 262
- **Total Published**: 262

### Ingestion Cohort Readiness Breakdown
- **Eligible & Classified**: 901
- **Low-Context Observation Count**: 330
- **Pending Classification**: 329
- **Failed Text Processing**: 0
- **Missing Text Processing**: 0

## 2. Matured Window Cohort Performance (Conversion Stable)
**Matured Window**: 2026-07-16T16:24:51Z to 2026-07-23T16:24:51Z

### Throughput Metrics
- **Total Ingested**: 1488
- **Low-Context Observation Count**: 370
- **Total Classified**: 1118
- **Relevant Classified**: 365
- **Total Curated**: 365
- **Curation Approved**: 309
- **Total Translated**: 309
- **Total Published**: 309

### Conversion Rates
- **Classification Rate**: 75.13%
- **Curation Rate**: 32.65%
- **Curation Approval Rate**: 84.66%
- **Translation Completion Rate**: 100.00%
- **Publication Rate**: 100.00%

### Ingestion Cohort Readiness Breakdown
- **Eligible & Classified**: 1118
- **Low-Context Observation Count**: 370
- **Pending Classification**: 369
- **Failed Text Processing**: 0
- **Missing Text Processing**: 0

## 3. Stage Latency Analysis (Seconds, Raw Window)

> [!WARNING]
> Latency metrics include system initialization/historical ingestion data and do not reflect steady-state operational SLA.

| Delay Type / Pipeline Stage | Avg Latency | Median (p50) | 90th Percentile (p90) |
| :--- | :--- | :--- | :--- |
| E2E Pipeline Lead Time | 404.23s | 324.00s | 445.90s |
| Feed Freshness Delay | 1712557.61s | 49595.00s | 2143884.00s |
| Fetch Execution Latency | 4.82s | 1.00s | 13.00s |
| Classification Delay | 114.36s | 68.00s | 138.00s |
| Curation Delay | 77.92s | 75.00s | 153.50s |
| Translation Delay | 92.20s | 71.00s | 146.70s |
| Publish Delay | 94.47s | 75.00s | 151.00s |

## 4. Published Content by Language (Matured Window)
| Language Code | Published Count | Coverage Rate |
| :--- | :--- | :--- |
| zh | 309 | 100.00% |
| en | 309 | 100.00% |
| ja | 309 | 100.00% |

## 5. Data Quality Diagnostics
No data quality anomalies detected.
