# Analysis Module Boundaries

This document defines the system role, boundary principles, and operational limits of the `analysis` module.

## 1. System Role & Boundaries

The `analysis` module is a **read-only consumer** and a **cross-module analysis layer**.

### 1.1 Boundary Principles
*   **No Operational Execution**: The `analysis` module does not execute feed fetching (`ingest`), classification (`classify`), curation (`curate`), translation (`translate`), or publishing (`publish`).
*   **Decision Recommender, Not Owner**: The module outputs recommendations (e.g., source disabling suggestions or model downgrade proposals), but the responsibility of applying changes to configurations (such as `sources.yaml` in the `ingest` module) remains strictly with the respective module's operational workflow.
*   **No Canonical State Writes**: The module may write its own reports or temporary cache files, but it must never write to or modify canonical operational tables (such as `source_item`, `classification_result`, or `curation_decision`) to preserve data integrity.

### 1.2 External Configuration Mapping Constraints
*   **No Standalone Source Table**: The database `canonical.db` does not contain a standalone `source` table; it only registers `source_id` values in operational tables (like `source_item` and `fetch_attempt`).
*   **Static Config Resolution**: The `analysis` module must resolve source metadata (such as the source title, feed URL, category_id, or active/enabled status) by reading the external [sources.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/sources.yaml) and [categories.yaml](file:///C:/Users/user/Documents/exopolitics/modules/ingest/config/categories.yaml) configuration files from the `ingest` module's configuration directory.
*   **Memory Join**: These source attributes must be mapped in application memory during runtime and must not be queried via direct database joins.

### 1.3 Module Boundary Diagram
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

    db -.->|Read-Only Access| analysis[Analysis Module]
    yaml -.->|Read-Only Access| analysis
    
    subgraph Outputs [Module Outputs]
        analysis -->|Markdown Reports| reports_dir[reports/analysis/ Directory]
        analysis -->|JSON Metrics| json[Structured JSON]
    end

    subgraph Future Extensibility [Future Extensibility]
        json -.->|Query & Plot| dashboard[Dashboard Web UI Module]
    end
```
