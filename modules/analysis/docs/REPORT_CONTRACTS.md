# Report & Integration Contracts

This document defines the CLI commands, JSON schemas, output formats, and the boundary contracts for external consumers (like a future Dashboard).

---

## 1. CLI Interface Specification
The `analysis` module provides a command-line interface via `python -m modules.analysis.src.cli`.

### 1.1 Unified CLI Options
All report-generating subcommands must support these arguments:
*   `--days INTEGER`: Lookback window in days (default: `7`).
*   `--format [markdown|json]`: Output format (default: `markdown`).
*   `--output-dir PATH`: Directory where files are written (default: `reports/analysis/`).
*   `--stdout`: Prints report text to standard output instead of writing to disk.

### 1.2 Subcommands

#### 1.2.1 `analyze-sources`
Analyzes RSS source health and content quality.
*   **Input Data**: `fetch_attempt`, `fetch_run`, `source_state`, `source_item`, `source_item_text`, `classification_result`, `curation_decision`, `ingest_dedup_marker`.
*   **Output File**: [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md) (or JSON equivalent).

#### 1.2.2 `analyze-funnel`
Analyzes conversion rates and leakage bottlenecks across pipeline stages.
*   **Funnel Stages**: Ingested -> Low-Context Split -> Classified -> Curated -> Approved Content -> Translation -> Publish.
*   **Output File**: [PIPELINE_FUNNEL_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/PIPELINE_FUNNEL_REPORT.md) (or JSON equivalent).

#### 1.2.3 `analyze-translation`
Analyzes translation pipeline efficiency, failures, and latency.
*   **Input Data**: `translation_output`, `approved_content_record`.
*   **Output File**: [TRANSLATION_PERFORMANCE_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/TRANSLATION_PERFORMANCE_REPORT.md) (or JSON equivalent).

---

## 2. JSON Output Schema Contract

To ensure predictable consumption by automated processors and web UI dashboards, any command run with `--format json` must emit a JSON object matching this schema.

### 2.1 JSON Top-Level Structure
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AnalysisReport",
  "type": "object",
  "properties": {
    "report_type": { 
      "type": "string", 
      "enum": ["sources", "funnel", "translation"] 
    },
    "schema_version": { 
      "type": "string",
      "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$"
    },
    "generated_at": { 
      "type": "string", 
      "format": "date-time",
      "description": "ISO 8601 timestamp of report creation"
    },
    "lookback_days": { 
      "type": "integer",
      "minimum": 1
    },
    "window_start": { 
      "type": "string", 
      "format": "date-time",
      "description": "ISO 8601 timestamp of lookback start time"
    },
    "window_end": { 
      "type": "string", 
      "format": "date-time",
      "description": "ISO 8601 timestamp of lookback end time"
    },
    "metrics": {
      "type": "object",
      "description": "Aggregated overall pipeline KPIs"
    },
    "breakdowns": {
      "type": "array",
      "items": {
        "type": "object",
        "description": "Granular breakdowns grouped by dimensions like source_id or language_code"
      }
    }
  },
  "required": [
    "report_type", 
    "schema_version", 
    "generated_at", 
    "lookback_days", 
    "window_start", 
    "window_end", 
    "metrics", 
    "breakdowns"
  ]
}
```

### 2.2 Concrete JSON Output Examples

#### 2.2.1 `analyze-sources` JSON Example
```json
{
  "report_type": "sources",
  "schema_version": "1.0.0",
  "generated_at": "2026-07-06T02:00:00Z",
  "lookback_days": 7,
  "window_start": "2026-06-29T02:00:00Z",
  "window_end": "2026-07-06T02:00:00Z",
  "metrics": {
    "overall_fetch_success_rate": 0.942,
    "total_ingested_items": 1520,
    "low_context_bypass_rate": 0.125
  },
  "breakdowns": [
    {
      "source_id": 101,
      "fetch_success_rate": 1.0,
      "ingest_volume": 420,
      "relevance_rate": 0.72,
      "curation_approval_rate": 0.85,
      "overall_yield": 0.612,
      "classification_character_volume_proxy": 512400,
      "classification_filtering_overhead": 1.38,
      "decision_model": {
        "quadrant": "golden_source",
        "safeguards_triggered": ["AUTHORITY"]
      }
    },
    {
      "source_id": 102,
      "fetch_success_rate": 0.45,
      "ingest_volume": 12,
      "relevance_rate": 0.05,
      "curation_approval_rate": 0.0,
      "overall_yield": 0.0,
      "classification_character_volume_proxy": 14400,
      "classification_filtering_overhead": null,
      "decision_model": {
        "quadrant": null,
        "safeguards_triggered": ["CONNECTION_DIAGNOSTICS"]
      }
    }
  ]
}
```

#### 2.2.2 `analyze-funnel` JSON Example
```json
{
  "report_type": "funnel",
  "schema_version": "1.0.0",
  "generated_at": "2026-07-06T02:00:00Z",
  "lookback_days": 7,
  "window_start": "2026-06-29T02:00:00Z",
  "window_end": "2026-07-06T02:00:00Z",
  "metrics": {
    "total_ingested": 1520,
    "low_context_bypass_count": 190,
    "total_classified": 1330,
    "relevant_classified": 950,
    "total_curated": 950,
    "curation_approved": 620,
    "total_translated": 615,
    "total_published": 612
  },
  "breakdowns": [
    {
      "stage": "ingest",
      "count": 1520,
      "stage_conversion_rate": 1.0,
      "cumulative_yield": 1.0
    },
    {
      "stage": "classification",
      "count": 1330,
      "stage_conversion_rate": 0.875,
      "cumulative_yield": 0.875
    },
    {
      "stage": "curation",
      "count": 620,
      "stage_conversion_rate": 0.652,
      "cumulative_yield": 0.407
    }
  ]
}
```

#### 2.2.3 `analyze-translation` JSON Example
```json
{
  "report_type": "translation",
  "schema_version": "1.0.0",
  "generated_at": "2026-07-06T02:00:00Z",
  "lookback_days": 7,
  "window_start": "2026-06-29T02:00:00Z",
  "window_end": "2026-07-06T02:00:00Z",
  "metrics": {
    "overall_translation_success_rate": 0.985,
    "average_latency_seconds": 182
  },
  "breakdowns": [
    {
      "language_code": "zh",
      "translation_success_rate": 0.992,
      "average_latency_seconds": 124,
      "stale_rate": 0.005,
      "translation_character_volume_proxy": 1284000
    },
    {
      "language_code": "es",
      "translation_success_rate": 0.978,
      "average_latency_seconds": 240,
      "stale_rate": 0.021,
      "translation_character_volume_proxy": 1312000
    }
  ]
}
```

---

## 3. Dashboard Integration Contract
The `analysis` module exposes underlying Python querying functions and CLI JSON outputs as a stable data contract.

*   **Boundary separation**: The `dashboard` module (e.g. a Streamlit Web UI) is responsible *only* for rendering the UI (scatter charts, bar charts) and reading JSON payloads.
*   **No Direct SQL**: The dashboard must **never** implement direct SQL queries or metric formulas. It must consume the JSON reports generated by the `analysis` module to prevent duplication of logic.
*   **Versioning rule**: Any backward-incompatible JSON shape change must increment `schema_version` and be documented in this file before implementation ships.
