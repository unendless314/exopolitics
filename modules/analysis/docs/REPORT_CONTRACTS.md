# Report & Integration Contracts

This document defines the JSON output schemas, versioning policies, and integration contracts for downstream consumers (such as a Web UI Dashboard).

For command-line invocation arguments and runner policies, refer to [EXECUTION_POLICY.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/EXECUTION_POLICY.md).

---

## 1. Report Families

The `analysis` module generates five stable report families:

1.  **Sources Report (`sources`)**: Evaluates RSS source connection health, category distribution, content density, and quadrant classification.
2.  **Funnel Report (`funnel`)**: Details throughput volume, stage-by-stage conversion rates, and latency bottlenecks under raw and matured windows.
3.  **Translation Report (`translation`)**: Monitors multilingual translation success rates, language volume proxies, and delays.
4.  **Classification Report (`classify`)**: Monitors LLM classification workload volume, relevance rate, and content density.
5.  **Curation Diagnostics Report (`curation_diagnostics`)**: Monitors curation LLM workload volume, approval rates, rejection reason mixes, and latency delays.

---

## 2. JSON Output Schema Contract

To ensure stable consumption by automated dashboards, any report generated with `--format json` must conform to the following schema structure.

### 2.1 JSON Top-Level Structure
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "AnalysisReport",
  "type": "object",
  "properties": {
    "report_type": {
      "type": "string",
      "enum": ["sources", "funnel", "translation", "classify", "curation_diagnostics"]
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
      "description": "ISO 8601 timestamp of lookback start time (for reports retaining it)"
    },
    "window_end": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 timestamp of lookback end time (for reports retaining it)"
    },
    "metrics": {
      "type": "object",
      "description": "Aggregated overall pipeline KPIs (for reports retaining it)"
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
    "lookback_days"
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
              "overall_fetch_success_rate": { "type": ["number", "null"] },
              "total_ingested_items": { "type": "integer" },
              "low_context_bypass_rate": { "type": ["number", "null"] }
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
                    "core": { "type": ["number", "null"] },
                    "adjacent": { "type": ["number", "null"] },
                    "irrelevant": { "type": ["number", "null"] },
                    "unknown": { "type": ["number", "null"] }
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
                },
                "text_processing_reason_distribution": {
                  "type": "object",
                  "description": "Optional diagnostic count breakdown by text_processing_reason. Keys may be omitted when a reason does not appear in the current window. Covers both low_context and failed status families.",
                  "properties": {
                    "missing_body": { "type": "integer" },
                    "sanitizer_exception": { "type": "integer" },
                    "post_cleanup_empty": { "type": "integer" },
                    "too_short": { "type": "integer" },
                    "title_only": { "type": "integer" },
                    "title_heavy": { "type": "integer" },
                    "template_heavy": { "type": "integer" },
                    "mostly_links": { "type": "integer" },
                    "truncated_to_low_context": { "type": "integer" }
                  },
                  "additionalProperties": false
                }
              },
              "required": [
                "source_id", "fetch_success_rate", "ingest_volume", "relevance_rate",
                "curation_approval_rate", "overall_yield", "classification_character_volume_proxy",
                "curation_character_volume_proxy", "classification_filtering_overhead", "topic_class_breakdown", "decision_model"
              ]
            }
          }
        },
        "required": ["window_start", "window_end", "metrics", "breakdowns"]
      }
    },
    {
      "if": {
        "properties": { "report_type": { "const": "funnel" } }
      },
      "then": {
        "properties": {
          "maturation_offset_hours": { "type": "integer" },
          "raw_window": {
            "type": "object",
            "properties": {
              "start": { "type": "string", "format": "date-time" },
              "end": { "type": "string", "format": "date-time" }
            },
            "required": ["start", "end"]
          },
          "matured_window": {
            "type": "object",
            "properties": {
              "start": { "type": "string", "format": "date-time" },
              "end": { "type": "string", "format": "date-time" }
            },
            "required": ["start", "end"]
          },
          "raw_metrics": {
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
              "classification_readiness_breakdown": {
                "type": "object",
                "properties": {
                  "low_context_bypass": { "type": "integer" },
                  "total_classified": { "type": "integer" },
                  "pending_classification": { "type": "integer" },
                  "failed_text_processing": { "type": "integer" },
                  "missing_text_processing": { "type": "integer" }
                },
                "required": [
                  "low_context_bypass", "total_classified", "pending_classification",
                  "failed_text_processing", "missing_text_processing"
                ]
              }
            },
            "required": [
              "total_ingested", "low_context_bypass_count", "total_classified",
              "relevant_classified", "total_curated", "curation_approved",
              "total_translated", "total_published", "classification_readiness_breakdown"
            ]
          },
          "matured_metrics": {
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
              "classification_rate": { "type": ["number", "null"] },
              "curation_rate": { "type": ["number", "null"] },
              "curation_approval_rate": { "type": ["number", "null"] },
              "translation_rate": { "type": ["number", "null"] },
              "publish_rate": { "type": ["number", "null"] },
              "classification_readiness_breakdown": {
                "type": "object",
                "properties": {
                  "low_context_bypass": { "type": "integer" },
                  "total_classified": { "type": "integer" },
                  "pending_classification": { "type": "integer" },
                  "failed_text_processing": { "type": "integer" },
                  "missing_text_processing": { "type": "integer" }
                },
                "required": [
                  "low_context_bypass", "total_classified", "pending_classification",
                  "failed_text_processing", "missing_text_processing"
                ]
              }
            },
            "required": [
              "total_ingested", "low_context_bypass_count", "total_classified",
              "relevant_classified", "total_curated", "curation_approved",
              "total_translated", "total_published", "classification_rate",
              "curation_rate", "curation_approval_rate", "translation_rate",
              "publish_rate", "classification_readiness_breakdown"
            ]
          },
          "raw_latency_metrics": {
            "type": "object",
            "properties": {
              "pipeline_lead_time_seconds": {
                "type": "object",
                "properties": {
                  "average": { "type": ["number", "null"] },
                  "median": { "type": ["number", "null"] },
                  "p90": { "type": ["number", "null"] }
                },
                "required": ["average", "median", "p90"]
              },
              "stage_latency_breakdown_seconds": {
                "type": "object",
                "properties": {
                  "feed_freshness_delay": {
                    "type": "object",
                    "properties": { "average": { "type": ["number", "null"] }, "median": { "type": ["number", "null"] }, "p90": { "type": ["number", "null"] } },
                    "required": ["average", "median", "p90"]
                  },
                  "fetch_execution_latency": {
                    "type": "object",
                    "properties": { "average": { "type": ["number", "null"] }, "median": { "type": ["number", "null"] }, "p90": { "type": ["number", "null"] } },
                    "required": ["average", "median", "p90"]
                  },
                  "classification_delay": {
                    "type": "object",
                    "properties": { "average": { "type": ["number", "null"] }, "median": { "type": ["number", "null"] }, "p90": { "type": ["number", "null"] } },
                    "required": ["average", "median", "p90"]
                  },
                  "curation_delay": {
                    "type": "object",
                    "properties": { "average": { "type": ["number", "null"] }, "median": { "type": ["number", "null"] }, "p90": { "type": ["number", "null"] } },
                    "required": ["average", "median", "p90"]
                  },
                  "translation_delay": {
                    "type": "object",
                    "properties": { "average": { "type": ["number", "null"] }, "median": { "type": ["number", "null"] }, "p90": { "type": ["number", "null"] } },
                    "required": ["average", "median", "p90"]
                  },
                  "publish_delay": {
                    "type": "object",
                    "properties": { "average": { "type": ["number", "null"] }, "median": { "type": ["number", "null"] }, "p90": { "type": ["number", "null"] } },
                    "required": ["average", "median", "p90"]
                  }
                },
                "required": [
                  "feed_freshness_delay", "fetch_execution_latency", "classification_delay",
                  "curation_delay", "translation_delay", "publish_delay"
                ]
              }
            },
            "required": ["pipeline_lead_time_seconds", "stage_latency_breakdown_seconds"]
          },
          "published_by_language": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "language_code": { "type": "string" },
                "published_count": { "type": "integer" },
                "coverage_rate": { "type": ["number", "null"] }
              },
              "required": ["language_code", "published_count", "coverage_rate"]
            }
          },
          "data_quality_anomalies": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "code": { "type": "string" },
                "count": { "type": "integer" },
                "item_samples": { "type": "array", "items": { "type": "integer" } }
              },
              "required": ["code", "count", "item_samples"]
            }
          }
        },
        "required": [
          "maturation_offset_hours", "raw_window", "matured_window",
          "raw_metrics", "matured_metrics", "raw_latency_metrics",
          "published_by_language", "data_quality_anomalies"
        ]
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
              "overall_translation_success_rate": { "type": ["number", "null"] },
              "overall_translation_completion_rate": { "type": ["number", "null"] },
              "average_latency_seconds": { "type": ["number", "null"] }
            },
            "required": ["overall_translation_success_rate", "overall_translation_completion_rate", "average_latency_seconds"]
          },
          "breakdowns": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "language_code": { "type": "string" },
                "translation_success_rate": { "type": ["number", "null"] },
                "translation_completion_rate": { "type": ["number", "null"] },
                "average_latency_seconds": { "type": ["number", "null"] },
                "stale_rate": { "type": ["number", "null"] },
                "translation_character_volume_proxy": { "type": "integer" }
              },
              "required": ["language_code", "translation_success_rate", "translation_completion_rate", "average_latency_seconds", "stale_rate", "translation_character_volume_proxy"]
            }
          }
        },
        "required": ["window_start", "window_end", "metrics", "breakdowns"]
      }
    },
    {
      "if": {
        "properties": { "report_type": { "const": "classify" } }
      },
      "then": {
        "properties": {
          "metrics": {
            "type": "object",
            "properties": {
              "total_classified": { "type": "integer" },
              "classification_character_volume_proxy": { "type": "integer" },
              "relevance_rate": { "type": ["number", "null"] },
              "average_confidence": { "type": ["number", "null"] },
              "overall_topic_class_breakdown": {
                "type": "object",
                "properties": {
                  "core": { "type": ["number", "null"] },
                  "adjacent": { "type": ["number", "null"] },
                  "irrelevant": { "type": ["number", "null"] },
                  "unknown": { "type": ["number", "null"] }
                },
                "required": ["core", "adjacent", "irrelevant", "unknown"]
              }
            },
            "required": ["total_classified", "classification_character_volume_proxy", "relevance_rate", "average_confidence", "overall_topic_class_breakdown"]
          },
          "breakdowns": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "source_id": { "type": "integer" },
                "classify_volume": { "type": "integer" },
                "classification_character_volume_proxy": { "type": "integer" },
                "relevance_rate": { "type": ["number", "null"] },
                "average_confidence": { "type": ["number", "null"] },
                "content_density_distribution": {
                  "type": "object",
                  "properties": {
                    "low": { "type": "number" },
                    "medium": { "type": "number" },
                    "high": { "type": "number" }
                  },
                  "required": ["low", "medium", "high"]
                },
                "topic_class_breakdown": {
                  "type": "object",
                  "properties": {
                    "core": { "type": ["number", "null"] },
                    "adjacent": { "type": ["number", "null"] },
                    "irrelevant": { "type": ["number", "null"] },
                    "unknown": { "type": ["number", "null"] }
                  },
                  "required": ["core", "adjacent", "irrelevant", "unknown"]
                }
              },
              "required": [
                "source_id", "classify_volume", "classification_character_volume_proxy",
                "relevance_rate", "average_confidence", "content_density_distribution",
                "topic_class_breakdown"
              ]
            }
          }
        },
        "required": ["window_start", "window_end", "metrics", "breakdowns"]
      }
    },
    {
      "if": {
        "properties": { "report_type": { "const": "curation_diagnostics" } }
      },
      "then": {
        "properties": {
          "metrics": {
            "type": "object",
            "properties": {
              "curation_approval_rate": { "type": ["number", "null"] },
              "curation_character_volume_proxy": { "type": "integer" },
              "curation_latency_seconds": {
                "type": "object",
                "properties": {
                  "average": { "type": ["number", "null"] },
                  "median": { "type": ["number", "null"] },
                  "p90": { "type": ["number", "null"] }
                },
                "required": ["average", "median", "p90"]
              }
            },
            "required": ["curation_approval_rate", "curation_character_volume_proxy", "curation_latency_seconds"]
          },
          "curation_rejection_mix": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "downstream_action": { "type": ["string", "null"] },
                "count": { "type": "integer" }
              },
              "required": ["downstream_action", "count"]
            }
          },
          "breakdowns": {
            "type": "array",
            "items": {
              "type": "object"
            }
          }
        },
        "required": ["window_start", "window_end", "metrics", "curation_rejection_mix", "breakdowns"]
      }
    }
  ]
}
```

---

## 3. Dashboard Integration Contract

The `analysis` module exposes query services and CLI JSON outputs as a stable data interface.

*   **Boundary Separation**: The downstream `dashboard` module (e.g. a Streamlit Web UI) is responsible *only* for reading JSON payloads and rendering visual plots.
*   **No Direct SQL**: The dashboard must **never** execute raw database queries or implement custom metric formulas. This prevents metric formula duplication and logic drift.
*   **Versioning Rule**: Any backward-incompatible JSON schema change must increment the `schema_version` (following Semantic Versioning: `MAJOR.MINOR.PATCH`) and be updated in this contract file before deployment.
