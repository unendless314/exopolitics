# Execution Policy

This document defines the CLI command-line options, runtime lookback semantics, file writing behavior, scheduling, and error fault-isolation policies for the `analysis` module.

---

## 1. CLI Execution Interface

The primary entry point for executing analytics queries and generating reports is:

```bash
python -m modules.analysis.src.cli [SUBCOMMAND] [OPTIONS]
```

### 1.1 Global Subcommand Options
All subcommands must accept and support the following arguments:

*   `--days INTEGER`: Lookback window in days (default: `7`). Defines the start boundary of analysis.
*   `--format [markdown|json]`: Determines the report output format (default: `markdown`).
*   `--output-dir PATH`: Directory where report files are written (default: `reports/analysis/`).
*   `--stdout`: If provided, prints the generated report text directly to standard output (stdout) and suppresses writing files to disk.
*   `--db-path PATH`: Path to the SQLite database file (default: `data/canonical.db`).

### 1.2 CLI Subcommand Surface

1.  **`analyze-sources`**: Analyzes RSS source health and content quality.
    *   *Output Target*: `SOURCE_QUALITY_REPORT.md` (or `.json`).
    *   *Parameters*: Supports optional overrides `--yield-threshold FLOAT` and `--relevance-threshold FLOAT`.
2.  **`analyze-funnel`**: Analyzes pipeline conversion rates, throughput, and bottleneck latencies.
    *   *Output Target*: `PIPELINE_FUNNEL_REPORT.md` (or `.json`).
3.  **`analyze-translation`**: Analyzes translation pipeline success rates, character volume, and latency.
    *   *Output Target*: `TRANSLATION_PERFORMANCE_REPORT.md` (or `.json`).
4.  **`analyze-classify`**: **[Phase 1 Implementation Candidate]** Analyzes LLM classification workload volume, relevance rate, and content density.
    *   *Output Target*: `CLASSIFY_MONITOR_REPORT.md` (or `.json`).

---

## 2. Runtime Window Semantics

To maintain mathematical consistency and prevent calculation drift, metrics in the [Metrics Catalog](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md) must follow one of these two lookback window semantics:

### 2.1 Cohort Window (`source_item_cohort`)
*   **Behavior**: The CLI lookback filter applies strictly to the base ingestion record timestamp (`source_item.fetched_at BETWEEN :start AND :end`).
*   **Rule**: Downstream actions (classification, curation, translation, publishing) relate to items belonging to this cohort. They are included in the metrics calculation *regardless of when the downstream events occurred*.
*   **Purpose**: Prevents mathematical leakage (e.g. overall yield exceeding 100% or conversion rates fluctuating due to timing offsets).

### 2.2 Event-Time Window (`event_time`)
*   **Behavior**: The CLI lookback filter applies to the timestamp column of the primary event table for that metric (e.g., `fetch_attempt.started_at`, `translation_output.updated_at`).
*   **Purpose**: Essential for monitoring real-time system performance, operational success rates, and processing workload volumes during the window.

---

## 3. File Writing & Idempotency Rules

*   **Target Directory**: By default, reports are written to the directory specified by `--output-dir` (default: `reports/analysis/`).
*   **Overwrite Behavior**: Execution is **idempotent**. Generating a report for the same `--days` window will overwrite the previously generated report file.
*   **Stdout Separation**: When `--stdout` is set, all report content must be printed to standard output. Log messages and diagnostics must be written strictly to standard error (`stderr`) to prevent JSON or Markdown corruption.

---

## 4. Fault Tolerance & Error Isolation

Because the `analysis` module is read-only, it must never block the core pipeline or crash due to database locks or configuration drift.

### 4.1 SQLite Database Locks
*   SQLite can experience locking when the ingest or translation modules write data during analysis.
*   **Policy**: The database execution engine must use a parameter-configured busy timeout (default: `10,000` ms / 10 seconds). If access cannot be obtained within the timeout, the CLI must log a warning and exit with code `1`, preventing deadlocks.

### 4.2 Missing Upstream Data
*   If a query returns zero rows or a database table is empty during the lookback window:
    *   The CLI must not crash.
    *   The report must still be generated, representing undefined rates as `NULL` or `0` and flagging the output metrics as `[INSUFFICIENT_DATA]`.

---

## 5. Scheduling & Logging Policy

### 5.1 Scheduling Assumptions
*   The `analysis` module runs as an asynchronous, periodic task (e.g., once daily at midnight) or ad-hoc by operators.
*   It operates after ingestion and publishing phases complete to capture full cohort throughput.

### 5.2 Logging Rules
*   **Logs Output**: Defaults to the console standard error (`stderr`). If persistent file logging is enabled, the output path must be dynamically resolved via configuration settings (such as `analysis_settings.yaml`) or environment variables (e.g. `ANALYSIS_LOG_PATH`), rather than relying on a hardcoded uncontracted local folder.
*   **Log Level**: Defaults to `INFO`, configurable via the `LOG_LEVEL` environment variable.
*   **Format**: Structured text with timestamp, log level, subcommand, and message.
