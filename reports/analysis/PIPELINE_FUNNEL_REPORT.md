# Pipeline Funnel Conversion & Bottleneck Report

**Generated At**: 2026-07-26T10:08:43Z
**Lookback Period**: 7 days
**Maturation Delay Offset**: 2 hours

## 1. Raw Window Cohort Performance
**Raw Window**: 2026-07-19T10:08:43Z to 2026-07-26T10:08:43Z

### Throughput Metrics
- **Total Ingested**: 5103
- **Low-Context Observation Count**: 1339
- **Total Classified**: 5102
- **Relevant Classified**: 3633
- **Total Curated**: 3633
- **Curation Approved**: 3015
- **Total Translated**: 3015
- **Total Published**: 3015

### Ingestion Cohort Readiness Breakdown
- **Eligible & Classified**: 5102
- **Low-Context Observation Count**: 1339
- **Pending Classification**: 0
- **Failed Text Processing**: 1
- **Missing Text Processing**: 0

## 2. Matured Window Cohort Performance (Conversion Stable)
**Matured Window**: 2026-07-19T08:08:43Z to 2026-07-26T08:08:43Z

### Throughput Metrics
- **Total Ingested**: 5103
- **Low-Context Observation Count**: 1339
- **Total Classified**: 5102
- **Relevant Classified**: 3633
- **Total Curated**: 3633
- **Curation Approved**: 3015
- **Total Translated**: 3015
- **Total Published**: 3015

### Conversion Rates
- **Classification Rate**: 99.98%
- **Curation Rate**: 71.21%
- **Curation Approval Rate**: 82.99%
- **Translation Completion Rate**: 100.00%
- **Publication Rate**: 100.00%

### Ingestion Cohort Readiness Breakdown
- **Eligible & Classified**: 5102
- **Low-Context Observation Count**: 1339
- **Pending Classification**: 0
- **Failed Text Processing**: 1
- **Missing Text Processing**: 0

## 3. Stage Latency Analysis (Seconds, Raw Window)

> [!WARNING]
> Latency metrics include system initialization/historical ingestion data and do not reflect steady-state operational SLA.

| Delay Type / Pipeline Stage | Avg Latency | Median (p50) | 90th Percentile (p90) |
| :--- | :--- | :--- | :--- |
| E2E Pipeline Lead Time | 4863.11s | 4862.00s | 4877.00s |
| Feed Freshness Delay | 37661616.34s | 13009642.00s | 105945061.80s |
| Fetch Execution Latency | 20.93s | 21.00s | 27.30s |
| Classification Delay | 369.49s | 363.00s | 557.00s |
| Curation Delay | 1016.62s | 1044.00s | 1114.00s |
| Translation Delay | 1750.72s | 1687.00s | 2074.10s |
| Publish Delay | 1790.22s | 1848.00s | 1981.00s |

## 4. Published Content by Language (Matured Window)
| Language Code | Published Count | Coverage Rate |
| :--- | :--- | :--- |
| zh | 3015 | 100.00% |
| en | 3015 | 100.00% |
| ja | 3015 | 100.00% |

## 5. Data Quality Diagnostics
No data quality anomalies detected.
