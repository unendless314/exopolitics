# Dashboard Design Document

This document defines the user-interface layout, component responsibilities, visualization choices, and technology stack for the `dashboard` module.

## 1. Design Goals

- **Operational clarity**: Surface pipeline health and source quality at a glance.
- **Actionable signals**: Highlight sources that need attention (low fetch success, dead weight, filtering burden).
- **Low maintenance**: Reuse the JSON contracts already defined by the `analysis` module; avoid custom metric logic in the UI layer.
- **Fast iteration**: Prioritize a working Streamlit MVP over a polished public website.

## 2. Technology Stack

| Layer | Choice | Rationale |
| :--- | :--- | :--- |
| UI Framework | Streamlit | Pure Python, rapid prototyping, built-in charts, ideal for internal operations dashboards. |
| Advanced Plots | Plotly | Used for interactive scatter plots, funnel charts, and latency distributions when Streamlit primitives are insufficient. |
| Data Loading | `pathlib` + `json` + Pydantic | Simple file reads validated against lightweight Pydantic models mirroring the `analysis` JSON schema. |
| Styling | Streamlit defaults + custom `config.toml` | Keeps the MVP lightweight; theming can be added later. |

## 3. Page Layout

The dashboard is a single-page app with a top navigation sidebar. Each view corresponds to one `analysis` report family.

```text
┌─────────────────────────────────────────────────────────────┐
│  UAP Aggregation Pipeline Dashboard                         │
│  Generated: 2026-07-16 10:29 UTC | Lookback: 7 days         │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│  Overview    │   [ KPI Cards ] [ Funnel Chart ]             │
│  Sources     │                                              │
│  Classify    │   [ Latency Bar Chart ]                      │
│  Translation │                                              │
│  Curation    │   [ Language Coverage Table ]                │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

### 3.1 Sidebar Navigation

- **Overview** (default): pipeline funnel and end-to-end latency.
- **Sources**: source health table, quadrant scatter plot, flag filtering.
- **Classify**: classification volume and relevance mix by source.
- **Translation**: language-level success, completion, latency, and volume.
- **Curation**: approval rate, rejection mix, and delay distribution.

## 4. View Specifications

### 4.1 Overview (Funnel Report)

Components:

1. **Header metadata**: report generation timestamp, lookback window, maturation offset.
2. **KPI cards**:
   - Total Ingested
   - Total Published
   - Classification Rate
   - Curation Approval Rate
   - Publication Rate
3. **Funnel chart**: horizontal bar chart showing counts at each stage.
   - Ingested → Classified → Relevant → Curated → Approved → Translated → Published
4. **Stage latency bar chart**: grouped bars for avg / p50 / p90 per stage.
5. **Language coverage table**: published count and coverage rate by language.
6. **Data quality anomalies**: small table or alert if anomalies exist.

### 4.2 Sources (Sources Report)

Components:

1. **Summary KPIs**:
   - Overall Fetch Success Rate
   - Total Ingested Items
   - Low-Context Observation Rate
2. **Source table**: sortable, searchable, with column filters.
   - Columns: Source ID, Source Title (if available in JSON), Fetch Success, Ingest Volume, Relevance Rate, Curation Approval, Overall Yield, Quadrant, Flags.
   - Note: the current `analysis` JSON output contains `source_id` only. Until `source_title` is added to the JSON schema, the table renders `source_id`. Human-readable titles can be added later without dashboard code changes once the JSON contract is enriched.
3. **Quadrant scatter plot**:
   - X-axis: Relevance Rate
   - Y-axis: Overall Yield
   - Color: Quadrant (`GOLDEN_SOURCE`, `FILTERING_BURDEN`, `DEAD_WEIGHT`, `NEEDLE_IN_A_HAYSTACK`, `N/A`)
   - Size: Ingest Volume
4. **Flag filter**: multi-select to show only sources flagged with `[AUTHORITY]`, `[CONNECTION_DIAGNOSTICS]`, `[INSUFFICIENT_DATA]`, etc.

### 4.3 Classify (Classification Report)

Components:

1. **Summary KPIs**:
   - Total Classified Items
   - Average Confidence
   - Relevance Rate
2. **Volume bar chart**: classify volume by source (top N + others).
3. **Relevance stacked bar chart**: Core / Adjacent / Irrelevant / Unknown mix per source.
4. **Confidence table**: average confidence per source, sortable.
5. **Content density distribution**: stacked bars for low / medium / high density per source.

### 4.4 Translation (Translation Report)

Components:

1. **Summary KPIs**:
   - Overall Translation Success Rate
   - Overall Translation Completion Rate
   - Average Latency
2. **Language comparison bar chart**: success rate and completion rate by language.
3. **Latency bar chart**: average latency by language.
4. **Volume table**: character volume proxy and stale rate by language.

### 4.5 Curation (Curation Diagnostics Report)

Components:

1. **Summary KPIs**:
   - Curation Approval Rate
   - Curation Character Volume Proxy
   - Average Delay
2. **Rejection mix pie chart**: `edit_rewrite` vs `reject_discard` counts.
3. **Latency table**: avg / median / p90 curation delay.

## 5. Component Responsibilities

Each view is implemented as a separate module under `src/components/`:

- `funnel_view.py`: render Overview page from `PIPELINE_FUNNEL_REPORT.json`.
- `sources_view.py`: render Sources page from `SOURCE_QUALITY_REPORT.json`.
- `classify_view.py`: render Classify page from `CLASSIFY_MONITOR_REPORT.json`.
- `translation_view.py`: render Translation page from `TRANSLATION_PERFORMANCE_REPORT.json`.
- `curation_view.py`: render Curation page from `CURATION_PERFORMANCE_REPORT.json`.

Common helpers live in `src/loaders/report_loader.py`:

- Discover report files in `reports/analysis/`.
- Validate `report_type` and `schema_version`.
- Convert JSON payloads into Pydantic models.
- Surface loading errors in the UI.

## 5.1 Theming Standards

The dashboard uses a consistent visual theme defined in `modules/dashboard/.streamlit/config.toml`:

```toml
[theme]
primaryColor = "#00A8E8"
backgroundColor = "#0E1117"
secondaryBackgroundColor = "#1E232B"
textColor = "#FAFAFA"
font = "sans serif"
```

Guidelines:

- Prefer Streamlit native components over custom HTML/CSS where possible.
- Custom CSS is limited to lightweight KPI card styling; avoid heavy visual effects that increase maintenance cost.
- Plotly figures use `template="plotly_dark"` with transparent backgrounds to match the theme.
- Keep the theme minimal; theming is an operational convenience, not a product differentiator.

## 6. Data Loading and Refresh Behavior

### 6.1 Caching

To avoid re-reading and re-parsing JSON files on every Streamlit interaction, all report loading is wrapped with `@st.cache_data`:

- Cache key is based on the file path and last-modified timestamp.
- Validation (schema version, report type) happens once per cache load.
- If a report file changes on disk, the cache is invalidated automatically by the timestamp change.

### 6.2 Refresh Behavior

- On app launch, all reports are loaded once and cached.
- A **Refresh Reports** button in the sidebar calls `st.cache_data.clear()` and reloads JSON files from disk.
- There is **no in-app report regeneration** in the MVP. Reports must be generated beforehand by running the `analysis` CLI.
- If operators need the absolute latest data, they can regenerate reports via the `analysis` CLI and then click **Refresh Reports** (or restart the Streamlit app).
- No automatic polling in the MVP; this can be added later if needed.

## 7. Error Handling and Empty States

- If a report file is missing, display a warning card and skip the corresponding sidebar section.
- If `schema_version` is unsupported, display a clear error with the expected version range.
- If a breakdown array is empty, render a friendly empty-state message instead of a blank chart.
- If a metric is `null` (e.g., `[INSUFFICIENT_DATA]`), render it as `N/A` with a tooltip explaining insufficient data.

## 8. Known Limitations and Dependencies

### Source Titles in JSON Output

The current `analysis` JSON reports for `sources` and `classify` include `source_id` but not `source_title`. This means the dashboard must display numeric source IDs in tables and charts until the `analysis` module enriches its JSON output. This is a deliberate dependency on the upstream `analysis` contract; the dashboard will not load `sources.yaml` directly to avoid coupling.

### Report Regeneration

The dashboard does not trigger `analysis` report generation. Operators must generate reports before launching the dashboard. The recommended workflow is:

```bash
python -m modules.analysis.src.cli analyze-funnel --format json
python -m modules.analysis.src.cli analyze-sources --format json
python -m modules.analysis.src.cli analyze-classify --format json
python -m modules.analysis.src.cli analyze-translation --format json
python -m modules.analysis.src.cli analyze-curation --format json
streamlit run modules/dashboard/src/app.py
```

## 9. Future Enhancements (Deferred)

The following are intentionally out of scope for the MVP:

- User authentication or role-based access control.
- Automatic scheduled refresh or real-time WebSocket updates.
- Public deployment; the dashboard is intended for internal operations.
- Advanced custom theming beyond the basic `config.toml` palette.
- Drill-down from summary charts into individual source items.
