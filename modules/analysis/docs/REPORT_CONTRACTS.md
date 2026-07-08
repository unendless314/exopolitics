# Report & Integration Contracts

This document defines the CLI commands, JSON schemas, output formats, and the boundary contracts for external consumers (like a future Dashboard).

---

## 1. CLI Interface Specification
The `analysis` module provides a command-line interface via `python -m modules.analysis.src.cli`.

### 1.1 Unified CLI Options
All report-generating subcommands must support these arguments:
*   `--days INTEGER`: Lookback window in days (default: `7`). This window defines the temporal boundary for analysis:
    - For metrics with **`event_time`** basis, `--days` restricts event timestamps (e.g. `fetch_attempt.created_at`, `translation_output.updated_at`, or `publish_record.published_at`) to the lookback period.
    - For metrics with **`source_item_cohort`** basis, `--days` restricts the base cohort ingestion time (`source_item.fetched_at`) to the lookback period. Downstream actions/states are included if they relate to items in this ingestion cohort, regardless of their own event timestamps.
*   `--format [markdown|json]`: Output format (default: `markdown`).
*   `--output-dir PATH`: Directory where files are written (default: `reports/analysis/`).
*   `--stdout`: Prints report text to standard output instead of writing to disk.

### 1.2 Subcommands

#### 1.2.1 `analyze-sources`
Analyzes RSS source health and content quality.
*   **Input Data**: `canonical.db` tables (`fetch_attempt`, `fetch_run`, `source_state`, `source_item`, `source_item_text`, `classification_result`, `curation_decision`, `ingest_dedup_marker`) and external configurations ([sources.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/sources.yaml), [categories.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/categories.yaml)).
*   **Subcommand-Specific Options**:
    *   `--yield-threshold FLOAT`: Optional override for Overall Yield threshold parameter (default: loads from `analysis_settings.yaml`).
    *   `--relevance-threshold FLOAT`: Optional override for Relevance Rate threshold parameter (default: loads from `analysis_settings.yaml`).
*   **Output File**: [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md) (or JSON equivalent).

#### 1.2.2 `analyze-funnel`
Analyzes conversion rates, stage delivery speeds, and leakage bottlenecks across pipeline stages.
*   **Funnel Stages**: Ingested -> Low-Context Split -> Classified -> Curated -> Approved Content -> Translation -> Publish.
*   **Latency Breakdown**: Outputs the overall Pipeline Lead Time (average, median, and p90) and the Pipeline Stage Latency Suite breakdown for each stage.
*   **Output File**: [PIPELINE_FUNNEL_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/PIPELINE_FUNNEL_REPORT.md) (or JSON equivalent).

#### 1.2.3 `analyze-translation`
Analyzes translation pipeline efficiency, failures, and latency (Translation Latency / Delay).
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
      "description": "Granular breakdowns grouped by dimensions like source_id or language_code"
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
  ],
  "allOf": [
    {
      "if": {
        "properties": { "report_type": { "const": "sources" } }
      },
      "then": {
        "properties": {
          "metrics": {
            "type": "object",
            "properties": {
              "overall_fetch_success_rate": { "type": "number" },
              "total_ingested_items": { "type": "integer" },
              "low_context_bypass_rate": { "type": "number" }
            },
            "required": ["overall_fetch_success_rate", "total_ingested_items", "low_context_bypass_rate"]
          },
          "breakdowns": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "source_id": { "type": "integer" },
                "fetch_success_rate": { "type": ["number", "null"] },
                "ingest_volume": { "type": "integer" },
                "relevance_rate": { "type": ["number", "null"] },
                "curation_approval_rate": { "type": ["number", "null"] },
                "overall_yield": { "type": ["number", "null"] },
                "classification_character_volume_proxy": { "type": "integer" },
                "curation_character_volume_proxy": { "type": "integer" },
                "classification_filtering_overhead": { "type": ["number", "null"] },
                "topic_class_breakdown": {
                  "type": "object",
                  "properties": {
                    "core": { "type": "number" },
                    "adjacent": { "type": "number" },
                    "irrelevant": { "type": "number" },
                    "unknown": { "type": "number" }
                  },
                  "required": ["core", "adjacent", "irrelevant", "unknown"]
                },
                "decision_model": {
                  "type": "object",
                  "properties": {
                    "quadrant": { "type": ["string", "null"] },
                    "analysis_flags": { "type": "array", "items": { "type": "string" } }
                  },
                  "required": ["quadrant", "analysis_flags"]
                }
              },
              "required": [
                "source_id", "fetch_success_rate", "ingest_volume", "relevance_rate", 
                "curation_approval_rate", "overall_yield", "classification_character_volume_proxy", 
                "curation_character_volume_proxy", "classification_filtering_overhead", "topic_class_breakdown", "decision_model"
              ]
            }
          }
        }
      }
    },
    {
      "if": {
        "properties": { "report_type": { "const": "funnel" } }
      },
      "then": {
        "properties": {
          "metrics": {
            "type": "object",
            "properties": {
              "total_ingested": { "type": "integer" },
              "low_context_bypass_count": { "type": "integer" },
              "total_classified": { "type": "integer" },
              "relevant_classified": { "type": "integer" },
              "total_curated": { "type": "integer" },
              "curation_approved": { "type": "integer" },
              "total_translated": { "type": "integer" },
              "total_published": { "type": "integer" },
              "pipeline_lead_time_seconds": {
                "type": "object",
                "properties": {
                  "average": { "type": "number" },
                  "median": { "type": "number" },
                  "p90": { "type": "number" }
                },
                "required": ["average", "median", "p90"]
              }
            },
            "required": [
              "total_ingested", "low_context_bypass_count", "total_classified", 
              "relevant_classified", "total_curated", "curation_approved", 
              "total_translated", "total_published", "pipeline_lead_time_seconds"
            ]
          },
          "stage_latency_breakdown_seconds": {
            "description": "Granular latency breakdown for each pipeline stage. To ensure direct comparability and statistical consistency, all stages in this breakdown—including feed freshness and fetch execution—are evaluated using the shared source_item_cohort basis (i.e. strictly for the items belonging to the ingestion cohort).",
            "type": "object",
            "properties": {
              "feed_freshness_delay": {
                "type": "object",
                "properties": { "average": { "type": "number" }, "median": { "type": "number" }, "p90": { "type": "number" } },
                "required": ["average", "median", "p90"]
              },
              "fetch_execution_latency": {
                "type": "object",
                "properties": { "average": { "type": "number" }, "median": { "type": "number" }, "p90": { "type": "number" } },
                "required": ["average", "median", "p90"]
              },
              "classification_delay": {
                "type": "object",
                "properties": { "average": { "type": "number" }, "median": { "type": "number" }, "p90": { "type": "number" } },
                "required": ["average", "median", "p90"]
              },
              "curation_delay": {
                "type": "object",
                "properties": { "average": { "type": "number" }, "median": { "type": "number" }, "p90": { "type": "number" } },
                "required": ["average", "median", "p90"]
              },
              "translation_delay": {
                "type": "object",
                "properties": { "average": { "type": "number" }, "median": { "type": "number" }, "p90": { "type": "number" } },
                "required": ["average", "median", "p90"]
              },
              "publish_delay": {
                "type": "object",
                "properties": { "average": { "type": "number" }, "median": { "type": "number" }, "p90": { "type": "number" } },
                "required": ["average", "median", "p90"]
              }
            },
            "required": [
              "feed_freshness_delay", "fetch_execution_latency", "classification_delay", 
              "curation_delay", "translation_delay", "publish_delay"
            ]
          },
          "breakdowns": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "stage": { "type": "string" },
                "count": { "type": "integer" },
                "stage_conversion_rate": { "type": "number" },
                "cumulative_yield": { "type": "number" }
              },
              "required": ["stage", "count", "stage_conversion_rate", "cumulative_yield"]
            }
          },
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
        },
        "required": ["metrics", "stage_latency_breakdown_seconds", "breakdowns", "published_by_language"]
      }
    },
    {
      "if": {
        "properties": { "report_type": { "const": "translation" } }
      },
      "then": {
        "properties": {
          "metrics": {
            "type": "object",
            "properties": {
              "overall_translation_success_rate": { "type": "number" },
              "average_latency_seconds": { "type": "number" }
            },
            "required": ["overall_translation_success_rate", "average_latency_seconds"]
          },
          "breakdowns": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "language_code": { "type": "string" },
                "translation_success_rate": { "type": "number" },
                "average_latency_seconds": { "type": "number" },
                "stale_rate": { "type": "number" },
                "translation_character_volume_proxy": { "type": "integer" }
              },
              "required": ["language_code", "translation_success_rate", "average_latency_seconds", "stale_rate", "translation_character_volume_proxy"]
            }
          }
        }
      }
    }
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
      "curation_character_volume_proxy": 320000,
      "classification_filtering_overhead": 1.38,
      "topic_class_breakdown": {
        "core": 0.50,
        "adjacent": 0.22,
        "irrelevant": 0.20,
        "unknown": 0.08
      },
      "decision_model": {
        "quadrant": "golden_source",
        "analysis_flags": ["AUTHORITY"]
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
      "curation_character_volume_proxy": 0,
      "classification_filtering_overhead": null,
      "topic_class_breakdown": {
        "core": 0.0,
        "adjacent": 0.05,
        "irrelevant": 0.90,
        "unknown": 0.05
      },
      "decision_model": {
        "quadrant": null,
        "analysis_flags": ["CONNECTION_DIAGNOSTICS"]
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
    "total_published": 612,
    "pipeline_lead_time_seconds": {
      "average": 845.5,
      "median": 450.0,
      "p90": 2400.0
    }
  },
  "stage_latency_breakdown_seconds": {
    "feed_freshness_delay": {
      "average": 1200.0,
      "median": 600.0,
      "p90": 3600.0
    },
    "fetch_execution_latency": {
      "average": 1.2,
      "median": 0.8,
      "p90": 2.5
    },
    "classification_delay": {
      "average": 15.4,
      "median": 10.0,
      "p90": 45.0
    },
    "curation_delay": {
      "average": 7200.0,
      "median": 1800.0,
      "p90": 21600.0
    },
    "translation_delay": {
      "average": 120.0,
      "median": 45.0,
      "p90": 300.0
    },
    "publish_delay": {
      "average": 5.2,
      "median": 3.0,
      "p90": 10.0
    }
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
  ],
  "published_by_language": [
    {
      "language_code": "en",
      "published_count": 612,
      "coverage_rate": 1.0000
    },
    {
      "language_code": "zh",
      "published_count": 610,
      "coverage_rate": 0.9967
    },
    {
      "language_code": "ja",
      "published_count": 605,
      "coverage_rate": 0.9886
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
