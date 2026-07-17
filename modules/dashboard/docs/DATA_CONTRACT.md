# Dashboard Data Contract

This document defines how the `dashboard` module consumes the structured JSON reports produced by the `analysis` module. It is the integration contract between the two modules.

## 1. Data Source

The dashboard reads **only** from the JSON files emitted by `modules/analysis/src/cli.py` with `--format json`.

**Pre-generation requirement**: Reports must be generated before the dashboard is launched. The dashboard does not invoke the `analysis` CLI or regenerate reports. To refresh data, operators regenerate reports via the `analysis` CLI and then restart or refresh the dashboard.

Expected report files:

| Report Family | JSON Filename | Report Type (`report_type`) |
| :--- | :--- | :--- |
| Sources | `SOURCE_QUALITY_REPORT.json` | `sources` |
| Funnel | `PIPELINE_FUNNEL_REPORT.json` | `funnel` |
| Translation | `TRANSLATION_PERFORMANCE_REPORT.json` | `translation` |
| Classification | `CLASSIFY_MONITOR_REPORT.json` | `classify` |
| Curation Diagnostics | `CURATION_PERFORMANCE_REPORT.json` | `curation_diagnostics` |

Default location: `reports/analysis/` relative to the repository root.

## 2. Boundary Rules

- **No direct database access**: The dashboard must never open `canonical.db` or execute raw SQL.
- **No metric recomputation**: The dashboard must use metrics exactly as they appear in the JSON payload. Do not re-derive percentages, averages, or conversion rates in the UI layer.
- **No operational writes**: The dashboard must not modify operational module configs, the database, or report files.
- **JSON is the single source of truth**: All charts, tables, and KPIs are rendered from JSON payloads.

## 3. File Discovery

The dashboard loader resolves the report directory as follows:

1. Use `settings.paths.report_dir` from `modules/dashboard/config/dashboard_settings.yaml` if present.
2. Fall back to `reports/analysis/` relative to the repository root.
3. Allow override via environment variable `DASHBOARD_REPORT_DIR` for local development.

Report files are matched by filename (case-sensitive). Missing files are tolerated; the corresponding dashboard section displays a warning.

## 3.1 Caching

The dashboard caches loaded JSON payloads using Streamlit's `@st.cache_data` decorator:

- Cache invalidation is based on file path and last-modified timestamp.
- Validation occurs once per cache load.
- The **Refresh Reports** sidebar button clears the cache and forces a reload from disk.

## 4. Schema Version Policy

The dashboard declares the JSON schema versions it supports in `dashboard_settings.yaml`:

```yaml
supported_schema_versions:
  sources: "1.0.0"
  funnel: "2.0.0"
  translation: "1.0.0"
  classify: "2.0.0"
  curation_diagnostics: "2.0.0"
```

These versions must match the `schema_version` values emitted by the corresponding services in `modules/analysis/src/services/`.

### 4.1 Validation Rules

- Each loaded JSON payload must contain `report_type` and `schema_version`.
- `report_type` must match one of the five supported families.
- `schema_version` must be a valid SemVer string (`MAJOR.MINOR.PATCH`).
- If `schema_version` differs from the supported version, the dashboard behaves according to the compatibility rules below.

### 4.2 Compatibility Rules

| Scenario | Behavior |
| :--- | :--- |
| Major version matches, minor/patch differ | Load with a non-blocking warning. Forward-compatible additions should not break rendering. |
| Major version differs | Refuse to load the report and display an error. The dashboard must be updated before consuming a new major schema. |
| `schema_version` missing | Refuse to load and display an error. |
| Unknown `report_type` | Skip the file and log a warning. |

## 5. Required Fields and Null Handling

The dashboard relies on the JSON schemas defined in `modules/analysis/docs/REPORT_CONTRACTS.md`. Key handling rules:

- Metrics fields may be `null` when data is insufficient (e.g., `[INSUFFICIENT_DATA]`). The dashboard must render `null` as `N/A`.
- Breakdown arrays may be empty. The dashboard must render an empty-state message rather than failing.
- Optional fields not present in a payload must be treated as absent, not as an error.
- **Source titles**: the current `analysis` JSON output for `sources` and `classify` reports contains `source_id` only. The dashboard must render `source_id` when `source_title` is absent. When `source_title` is added to the JSON schema in the future, the dashboard should display it without requiring structural changes.

## 6. Report-to-View Mapping

Each JSON report drives exactly one dashboard view:

| JSON Report | View Module | Primary Visualizations |
| :--- | :--- | :--- |
| `PIPELINE_FUNNEL_REPORT.json` | `funnel_view.py` | KPI cards, funnel chart, latency bars, language table, anomaly alerts |
| `SOURCE_QUALITY_REPORT.json` | `sources_view.py` | Source table, quadrant scatter plot, flag filters |
| `CLASSIFY_MONITOR_REPORT.json` | `classify_view.py` | Volume bars, relevance mix, confidence table, density distribution |
| `TRANSLATION_PERFORMANCE_REPORT.json` | `translation_view.py` | Success/completion bars, latency bars, volume table |
| `CURATION_PERFORMANCE_REPORT.json` | `curation_view.py` | Approval KPI, rejection pie chart, latency table |

## 7. Error Handling

### 7.1 Missing Report File

- Display a warning card in the affected view.
- Continue rendering the rest of the dashboard.

### 7.2 Invalid JSON

- Display an error card with the file path and exception message.
- Do not crash the entire app.

### 7.3 Schema Version Mismatch

- Major mismatch: error card with instructions to update the dashboard.
- Minor/patch mismatch: warning banner with the loaded and expected versions.

### 7.4 Empty or Null Data

- Render "No data available for this lookback window" or equivalent.
- Disable interactive filters that depend on the missing data.

## 8. Environment Overrides

| Variable | Purpose |
| :--- | :--- |
| `DASHBOARD_REPORT_DIR` | Override the directory containing JSON report files. |
| `DASHBOARD_LOG_LEVEL` | Set Python logging level (default: `INFO`). |

## 9. Versioning and Change Management

- The `analysis` module owns the JSON schemas. Any backward-incompatible change must increment the major version of `schema_version` and be documented in `modules/analysis/docs/REPORT_CONTRACTS.md`.
- The `dashboard` module owns its supported-version list. When `analysis` introduces a compatible minor/patch change, the dashboard may update its supported version without code changes. When a major change occurs, the dashboard code must be updated.
- Both modules must agree on the supported schema versions before deployment.
