# Implementation Plan

This document details the code architecture, development phases, and testing strategy for the `analysis` module.

---

## 1. Code Architecture (CLI-Service-Query)

To ensure the analytics logic remains decoupling and maintainable as metrics grow, the code structure utilizes a three-tier architecture: **CLI -> Service -> Query**.

```text
modules/analysis/
├── config/
│   └── analysis_settings.yaml   # Module threshold configurations
├── docs/
│   └── ...                      # Technical specifications
├── src/
│   ├── __init__.py
│   ├── cli.py                   # CLI entrypoint (registers subcommands)
│   ├── config.py                # Parses analysis_settings.yaml
│   ├── database.py              # SQLite helper with timeout parameters
│   ├── queries/                 # SQL query files (pure SQL; no business logic)
│   │   ├── __init__.py
│   │   ├── ingest_queries.py
│   │   ├── classify_queries.py
│   │   ├── curate_queries.py
│   │   ├── translate_queries.py
│   │   ├── publish_queries.py
│   │   └── aggregation_queries.py
│   └── services/                # Computation & Decision engines
│       ├── __init__.py
│       ├── ingest_service.py
│       ├── classify_service.py
│       ├── curate_service.py
│       ├── translate_service.py
│       ├── publish_service.py
│       ├── source_classifier.py # Source Quadrant Classifier logic
│       └── funnel_calculator.py # p50/p90 stage latency calculator
└── tests/                       # Unit and integration test suites
    ├── __init__.py
    ├── conftest.py              # Mock database fixtures
    ├── test_classify_service.py
    ├── test_source_classifier.py
    └── test_funnel_calculator.py
```

### 1.1 Architecture Layer Rules
*   **Queries Layer (`queries/`)**: Contains only raw SQL query execution functions. They return raw database rows (tuples or dicts). No metric formulas, thresholds, or formatting logic is permitted here.
*   **Services Layer (`services/`)**: Applies formulas, parses additional signals, executes the Source Quadrant Classifier, maps configuration metadata in memory, and formats result objects.
*   **CLI Layer (`cli.py`)**: Responsible only for parsing CLI arguments, loading configurations, invoking services, and printing results (or writing output files).

---

## 2. Phased Development Roadmap

### Phase 1: Foundation & Classification Cost Center
*   **Objective**: Implement core CLI structures and prioritize the `classify` module monitor (`analyze-classify`), which is the pipeline's largest API token cost center.
*   **Tasks**:
    1.  Create `database.py` with retry logic for SQLite busy timeout (10 seconds).
    2.  Create `config.py` to parse thresholds from `analysis_settings.yaml`.
    3.  Implement `classify_queries.py` and `classify_service.py` to query character volume proxies, relevance rates, density distributions, and classification confidence.
    4.  Implement the initial CLI with the `analyze-classify` subcommand.

### Phase 2: Aggregation Reporting & Decision Engine
*   **Objective**: Establish cross-module funnel conversion and the source classification heuristics.
*   **Tasks**:
    1.  Implement `source_classifier.py` containing the Source Quadrant Classifier algorithm and isolation safeguards (Fetch success < 50%, zero-ingestion null handling).
    2.  Implement `aggregation_queries.py` and `funnel_calculator.py` to calculate E2E pipeline lead time and segment-by-segment latency percentiles (p50/p90).
    3.  Register subcommands `analyze-sources` and `analyze-funnel` in `cli.py`.

### Phase 3: Diagnostic Extension & Automation
*   **Objective**: Complete single-module diagnostics and prepare for dashboard integration.
*   **Tasks**:
    1.  Implement `ingest_service.py`, `curate_service.py`, `translate_service.py`, and `publish_service.py`.
    2.  Register subcommand `analyze-translation` in `cli.py`.
    3.  Export structured JSON output according to schemas defined in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md).

---

## 3. Testing & Validation Strategy

No automated test suite has been committed yet. When executable code is added under `modules/analysis/src/`, test files must be created under `modules/analysis/tests/` matching the source structure.

### 3.1 Unit Testing with Mock In-Memory Database
*   **Fixture Setup**: Use `pytest` fixtures to instantiate a temporary, in-memory SQLite connection (`sqlite3.connect(':memory:')`).
*   **Schema Creation**: Execute DDL statements to build tables mirroring `canonical.db` (such as `source_item`, `classification_result`, `curation_decision`).
*   **Mock Cohort Seeding**: Populate the mock tables with specific test data, such as:
    *   A cohort of sources with high classification volume but zero relevance (tests "Filtering Burden").
    *   A cohort of sources with less than 50% fetch success rate (tests "Fetch Health Isolation").
    *   A cohort with zero fetched items (tests "Zero Ingestion Null-Handling").
*   **Assertions**: Verify that query services return mathematically correct calculations (matching the formulas in [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md)) and correct quadrant tags (matching [DECISION_MODELS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DECISION_MODELS.md)).

### 3.2 Output Validation
Before pushing changes to production, developers must run the CLI and validate:
1.  **Markdown Rendering**: Verify Markdown reports are properly formatted and readable on standard terminals.
2.  **JSON Validation**: Run the CLI with the `--format json` option and validate the output payload against the JSON Schema defined in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) using a JSON validator (e.g. `jsonschema` library).
