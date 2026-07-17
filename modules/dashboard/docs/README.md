# Dashboard Module

The `dashboard` module is a read-only visualization layer for the operational reports emitted by the `analysis` module. It consumes the structured JSON payloads written to `reports/analysis/` and renders them as interactive charts and tables for pipeline monitoring.

## Scope

- Render pipeline funnel, source health, classification workload, translation status, and curation diagnostics.
- Provide interactive filtering, sorting, and date-range selection where applicable.
- Remain strictly read-only: no database writes, no metric recomputation, no operational execution.

## Non-Goals

- Fetch RSS feeds, run LLM classification, curate content, translate text, or publish output.
- Execute raw SQL queries against `canonical.db`.
- Modify operational module configurations (`sources.yaml`, `publish_settings.yaml`, etc.).

## Architecture

```text
modules/dashboard/
├── .streamlit/
│   └── config.toml               # Streamlit theme (picked up when launched from this directory)
├── config/
│   └── dashboard_settings.yaml   # Module-level UI and path settings
├── docs/
│   ├── README.md                 # This file
│   ├── DASHBOARD_DESIGN.md       # Page layout, component map, tech choices
│   └── DATA_CONTRACT.md          # JSON consumption rules and schema-version policy
├── requirements.txt              # UI dependencies (streamlit, plotly, pandas, pydantic, pyyaml)
├── src/
│   ├── __init__.py
│   ├── app.py                    # Streamlit entrypoint
│   ├── loaders/
│   │   ├── __init__.py
│   │   └── report_loader.py      # JSON loader, Pydantic models, and schema validator
│   └── components/
│       ├── __init__.py
│       ├── formatting.py         # Shared display formatting helpers (N/A, percent, durations)
│       ├── funnel_view.py
│       ├── sources_view.py
│       ├── classify_view.py
│       ├── translation_view.py
│       └── curation_view.py
└── tests/
    ├── __init__.py
    └── test_report_loader.py
```

## Quick Start

```bash
# 1. Generate JSON reports via the analysis module
python -m modules.analysis.src.cli analyze-funnel --format json --days 7
python -m modules.analysis.src.cli analyze-sources --format json --days 7
python -m modules.analysis.src.cli analyze-classify --format json --days 7
python -m modules.analysis.src.cli analyze-translation --format json --days 7
python -m modules.analysis.src.cli analyze-curation --format json --days 7

# 2. Install dependencies (a repo-level .venv is recommended)
pip install -r modules/dashboard/requirements.txt

# 3. Launch the dashboard from the module directory so the theme is applied
cd modules/dashboard
streamlit run src/app.py
```

Run the loader unit tests from the repository root:

```bash
pytest modules/dashboard/tests/
```

## Relationship to Other Modules

- `analysis`: Data producer. The dashboard depends on the JSON schemas defined in `modules/analysis/docs/REPORT_CONTRACTS.md`.
- `site`: The dashboard is an internal operations tool. It does not write to the public site repository.
- `ingest/classify/curate/translate/publish`: Operational modules. The dashboard observes them through `analysis` outputs only.

## Documentation Index

- [DASHBOARD_DESIGN.md](file:///C:/Users/user/Documents/exopolitics/modules/dashboard/docs/DASHBOARD_DESIGN.md): UI layout, component responsibilities, and technology choices.
- [DATA_CONTRACT.md](file:///C:/Users/user/Documents/exopolitics/modules/dashboard/docs/DATA_CONTRACT.md): Rules for consuming `analysis` JSON reports, schema-version handling, and error behavior.
