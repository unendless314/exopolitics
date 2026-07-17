# Pipeline Funnel Conversion & Bottleneck Report

**Generated At**: 2026-07-17T08:12:47Z
**Lookback Period**: 7 days
**Maturation Delay Offset**: 2 hours

## 1. Raw Window Cohort Performance
**Raw Window**: 2026-07-10T08:12:47Z to 2026-07-17T08:12:47Z

### Throughput Metrics
- **Total Ingested**: 4842
- **Low-Context Bypass**: 886
- **Total Classified**: 3838
- **Relevant Classified**: 2922
- **Total Curated**: 2922
- **Curation Approved**: 2310
- **Total Translated**: 2310
- **Total Published**: 2306

### Ingestion Cohort Readiness Breakdown
- **Completed & Classified**: 3838
- **Low-Context Bypass**: 886
- **Pending Classification**: 117
- **Failed Text Processing**: 1
- **Missing Text Processing**: 0

## 2. Matured Window Cohort Performance (Conversion Stable)
**Matured Window**: 2026-07-10T06:12:47Z to 2026-07-17T06:12:47Z

### Throughput Metrics
- **Total Ingested**: 4827
- **Low-Context Bypass**: 882
- **Total Classified**: 3838
- **Relevant Classified**: 2922
- **Total Curated**: 2922
- **Curation Approved**: 2310
- **Total Translated**: 2310
- **Total Published**: 2306

### Conversion Rates
- **Classification Rate**: 79.51%
- **Curation Rate**: 76.13%
- **Curation Approval Rate**: 79.06%
- **Translation Completion Rate**: 100.00%
- **Publication Rate**: 99.83%

### Ingestion Cohort Readiness Breakdown
- **Completed & Classified**: 3838
- **Low-Context Bypass**: 882
- **Pending Classification**: 106
- **Failed Text Processing**: 1
- **Missing Text Processing**: 0

## 3. Stage Latency Analysis (Seconds, Raw Window)

> [!WARNING]
> Latency metrics include system initialization/historical ingestion data and do not reflect steady-state operational SLA.

| Delay Type / Pipeline Stage | Avg Latency | Median (p50) | 90th Percentile (p90) |
| :--- | :--- | :--- | :--- |
| E2E Pipeline Lead Time | 111991.00s | 112562.00s | 112575.00s |
| Feed Freshness Delay | 31804397.53s | 5690158.00s | 98305007.70s |
| Fetch Execution Latency | 8.72s | 11.00s | 19.00s |
| Classification Delay | 87889.45s | 81853.00s | 90376.30s |
| Curation Delay | 6461.61s | 8415.50s | 9423.00s |
| Translation Delay | 5973.93s | 5436.00s | 10559.00s |
| Publish Delay | 14611.39s | 15799.00s | 18982.00s |

## 4. Published Content by Language (Matured Window)
| Language Code | Published Count | Coverage Rate |
| :--- | :--- | :--- |
| zh | 2306 | 99.83% |
| en | 2306 | 99.83% |
| ja | 2306 | 99.83% |

## 5. Data Quality Diagnostics
No data quality anomalies detected.
