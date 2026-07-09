# Analysis Module Boundaries

This document defines the system role, boundary principles, and operational limits of the `analysis` module.

## 1. System Role & Boundaries

The `analysis` module is a **read-only analytics module** that supports both **module-specific operational analytics** and **cross-module aggregation analysis**.

### 1.1 Boundary Principles
*   **No Operational Execution**: The `analysis` module does not execute feed fetching (`ingest`), classification (`classify`), curation (`curate`), translation (`translate`), or publishing (`publish`).
*   **Centralized Analytics Ownership**: Monitoring, reporting, and performance-analysis logic should live in the `analysis` module rather than being duplicated across operational modules. This includes both single-module diagnostics and cross-module aggregation.
*   **Decision Recommender, Not Owner**: The module outputs recommendations (e.g., source disabling suggestions or model downgrade proposals), but the responsibility of applying changes to configurations (such as `sources.yaml` in the `ingest` module) remains strictly with the respective module's operational workflow.
*   **No Canonical State Writes**: The module may write its own reports or temporary cache files, but it must never write to or modify canonical operational tables (such as `source_item`, `classification_result`, or `curation_decision`) to preserve data integrity.
*   **Output Ownership**: The `analysis` module owns the emission of structured JSON payloads and Markdown reports under the dedicated [reports/analysis/](file:///C:/Users/user/Documents/exopolitics/reports/analysis/) directory. It does not write to the static site repository or downstream server paths directly.

### 1.2 Analytics Scope
The `analysis` module may expose two complementary classes of reporting:
*   **Module-Specific Analytics**: Read-only diagnostics focused on a single pipeline stage (for example, fetch health in `ingest`, classification workload volume in `classify`, or publish coverage in `publish`).
*   **Cross-Module Aggregation Analytics**: Read-only reports that correlate multiple stages to measure source ROI, funnel conversion, end-to-end latency, and other cross-stage operational outcomes.

### 1.3 External Configuration Mapping Constraints
*   **No Standalone Source Table**: The database `canonical.db` does not contain a standalone `source` table; it only registers `source_id` values in operational tables (like `source_item` and `fetch_attempt`).
*   **Static Config Resolution**: The `analysis` module must resolve source metadata (such as the source title, xml_url, category_id, or active/enabled status) by reading the external [sources.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/sources.yaml) and [categories.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/categories.yaml) configuration files from the `ingest` module's configuration directory.
*   **Memory Join**: These source attributes must be mapped in application memory during runtime and must not be queried via direct database joins. For detailed schemas and mapping structures, refer to [DATA_DEPENDENCIES.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/DATA_DEPENDENCIES.md).

### 1.4 Module Boundary Diagram
```mermaid
graph TD
    subgraph Configurations [Configurations]
        yaml[sources.yaml & categories.yaml]
    end

    subgraph Core Pipeline Modules [Core Pipeline Modules]
        ingest[Ingest Module] -->|Write| db[(canonical.db)]
        classify[Classify Module] -->|Write| db
        curate[Curate Module] -->|Write| db
        translate[Translate Module] -->|Write| db
    end

    db -.-->|Read-Only Access| analysis[Analysis Module]
    yaml -.-->|Read-Only Access| analysis
    
    subgraph Outputs [Module Outputs]
        analysis -->|Markdown Reports| reports_dir[reports/analysis/ Directory]
        analysis -->|JSON Metrics| json[Structured JSON]
    end

    subgraph Future Extensibility [Future Extensibility]
        json -.-->|Query & Plot| dashboard[Dashboard Web UI Module]
    end
```
