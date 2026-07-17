# Analysis Module Remediation Plan (Version 2.0)

This plan documents the remediation steps for the `analysis` module. The execution is structured into two distinct phases to isolate internal module enhancements from higher-risk cross-module database and workflow refactoring.

---

## Phase 1: Analysis Module Internal Remediation (Low Risk)

All work in this phase is strictly confined to the `analysis` module and will be executed immediately. For standard 7-day reports, historical latency outliers will be temporarily accepted, but clearly flagged in human-readable outputs.

### 1. Classification-Readiness State Breakdown & Single Snapshot Query

The 5-state breakdown represents the mutually exclusive text processing and readiness states of the ingestion cohort, rather than downstream funnel stages. To prevent race conditions during concurrent ingestion runs where multiple independent queries could yield unbalanced counts, this breakdown must be evaluated using a **single conditional aggregation query** under a single database read-snapshot.

1.  **Reconciliation Invariant**:
    $$\text{Total Ingested} = \text{Low-Context Bypass} + \text{Total Classified} + \text{Pending Classification} + \text{Failed Text Processing} + \text{Missing Text Processing}$$
2.  **State Definitions**:
    *   **Classified** (`total_classified`): Ingestion cohort items where `text_processing_status = 'completed'` and a corresponding `classification_result` row exists.
    *   **Low-Context Bypass** (`low_context_bypass_count`): Ingestion cohort items where `text_processing_status = 'low_context'`.
    *   **Pending Classification** (`pending_classification`): Ingestion cohort items where `text_processing_status = 'completed'` but no classification result has been written yet.
    *   **Failed Text Processing** (`failed_text_processing`): Ingestion cohort items where `text_processing_status = 'failed'`.
    *   **Missing Text Processing** (`missing_text_processing`): Ingestion cohort items that have no corresponding record in `source_item_text`.
3.  **SQL Query Design (in `modules/analysis/src/queries/aggregation_queries.py`)**:
    ```sql
    SELECT 
        COUNT(si.source_item_id) AS total_ingested,
        SUM(CASE WHEN sit.text_processing_status = 'low_context' THEN 1 ELSE 0 END) AS low_context_bypass,
        SUM(CASE WHEN sit.text_processing_status = 'completed' AND cr.source_item_id IS NOT NULL THEN 1 ELSE 0 END) AS total_classified,
        SUM(CASE WHEN sit.text_processing_status = 'completed' AND cr.source_item_id IS NULL THEN 1 ELSE 0 END) AS pending_classification,
        SUM(CASE WHEN sit.text_processing_status = 'failed' THEN 1 ELSE 0 END) AS failed_text_processing,
        SUM(CASE WHEN sit.source_item_id IS NULL THEN 1 ELSE 0 END) AS missing_text_processing
    FROM source_item si
    LEFT JOIN source_item_text sit ON si.source_item_id = sit.source_item_id
    LEFT JOIN classification_result cr ON si.source_item_id = cr.source_item_id
    WHERE si.fetched_at >= :start AND si.fetched_at < :end;
    ```
    *Note: Queries must use half-open intervals `[start, end)` to prevent double-counting boundary records between contiguous reporting windows.*

4.  **Diagnostic Anomaly Reporting (Data Quality)**:
    Instead of raising validation assertions that crash report generation, the service will capture anomalies (e.g. downstream results written for items missing completed texts) and output them inside the report payload under a `data_quality_anomalies` array.
    *   **Payload Schema**:
        Anomalies are represented as an array of objects:
        ```json
        "data_quality_anomalies": [
          {
            "code": "ORPHANED_CLASSIFICATION",
            "count": 1,
            "item_samples": [101]
          }
        ]
        ```
    *   **Anomalies Codes**:
        *   `ORPHANED_CLASSIFICATION`: `classification_result` exists but parent `source_item_text` is missing or status is not `'completed'`.
        *   `ORPHANED_CURATION`: `curation_decision` exists but parent `classification_result` is missing or topic_class is not core/adjacent.
        *   `MISSING_TEXT_RECORD`: `source_item` exists but no corresponding `source_item_text` row is written.
        *   `UNKNOWN_TEXT_PROCESSING_STATUS`: `source_item_text.text_processing_status` is not one of the three contracted states. This is a defensive diagnostic for legacy or corrupted databases and must be impossible under the production CHECK constraint.

5.  **Specification Changes**:
    *   Update [METRICS_CATALOG.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/METRICS_CATALOG.md) with cohort-bound formulas for `pending_classification`, `failed_text_processing`, and `missing_text_processing`.
    *   Update the `funnel` schema in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) to make all breakdown metrics required.

### 2. Maturation Delay Offset Configuration (Double Window)

To prevent recently ingested items still waiting in processing queues from skewing conversion rates, we introduce a double reporting window.

1.  **Double Window Mechanics**:
    *   Add `maturation_offset_hours` (default: `2`) to `modules/analysis/config/analysis_settings.yaml` under the `reporting.defaults` block.
    *   Extend the Pydantic configuration model `ReportingDefaults` in `modules/analysis/src/config.py` to support load validation of this parameter.
    *   Expose a CLI flag `--maturation-offset-hours` in `cli.py` to allow runtime configuration override.
    *   Define two distinct half-open windows, both maintaining the exact configured `days` duration:
        *   **Raw Window** (`raw_window`): `[window_start, window_end)`
        *   **Matured Window** (`matured_window`): `[window_start - offset, window_end - offset)`
    *   Calculate funnel metrics under both windows. Funnel conversion rates are only meaningful under the matured window.
2.  **No Latency Offset in Phase 1**:
    *   The maturation offset **will not** be applied to latency calculations in Phase 1. All E2E and stage latencies will remain evaluated on the raw lookback cohort.
    *   To prevent misinterpretation, a warning banner will be displayed in human-readable Markdown/CLI reports:
        > [!WARNING]
        > Latency metrics include system initialization/historical ingestion data and do not reflect steady-state operational SLA.
3.  **JSON Output Schema (Funnel 2.0.0 Contract)**:
    To enforce strong contract adherence and eliminate ambiguity:
    *   **Removed Legacy Fields**: The legacy top-level `metrics` field, `window_start`, and `window_end` are **completely removed** from Funnel v2 to avoid redundant or conflicting window ranges.
    *   **Breakdowns Refactoring**: The legacy top-level `breakdowns` array is removed. The mutually exclusive 5-state counts are emitted as `raw_metrics.classification_readiness_breakdown` and `matured_metrics.classification_readiness_breakdown`, so each breakdown has an unambiguous cohort window.
    *   **Shared Contract Refactoring**: Remove `window_start`, `window_end`, `metrics`, and `breakdowns` from the shared top-level required list. Require them only in report-family-specific schema branches that retain them; the Funnel v2 branch instead requires the fields listed below.
    *   **New Required Properties**:
        *   `maturation_offset_hours`: Integer value applied.
        *   `raw_window`: Object containing `{ "start": string, "end": string }`
        *   `matured_window`: Object containing `{ "start": string, "end": string }`
        *   `raw_metrics`: Object containing raw throughput counts of stages (e.g. `ingested`, `classified`, `curated`, `approved`, `translated`, `published`) and the raw `classification_readiness_breakdown`. **No conversion rates will be output under raw_metrics.**
        *   `matured_metrics`: Object containing throughput counts, the matured `classification_readiness_breakdown`, and conversion rates (e.g. `classification_rate`, `curation_approval_rate`, etc.) under the matured window.
        *   `raw_latency_metrics`: Required object containing the raw-window `pipeline_lead_time_seconds` and `stage_latency_breakdown_seconds`. It is never maturity-adjusted in Phase 1.

### 3. Markdown Topic Distribution & Curation Reports

To resolve information transparency gaps:
1.  **4-Class Topic Breakdown (Classify 2.0.0 Contract)**:
    *   Instead of flattening metrics to a single `Relevance Rate` percentage, both [CLASSIFY_MONITOR_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/CLASSIFY_MONITOR_REPORT.md) and [SOURCE_QUALITY_REPORT.md](file:///C:/Users/user/Documents/exopolitics/reports/analysis/SOURCE_QUALITY_REPORT.md) tables must present the full 4-class distribution:
        *   Classify Table Column: `Relevance Breakdown (Core / Adj / Irr / Unk)`
        *   Example Value: `90.00% (30.00% / 60.00% / 10.00% / 0.00%)` (relevance rate leading, followed by individual proportions).
    *   Modify `classify_queries.py` and `classify_service.py` to calculate and propagate `prop_core`, `prop_adjacent`, `prop_irrelevant`, and `prop_unknown`.
    *   Update [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) to make `overall_topic_class_breakdown`, per-source `topic_class_breakdown`, and the existing `classification_character_volume_proxy` **required** fields.
    *   *Note: The sources JSON payload structure is unchanged; only its Markdown formatter is updated to print workload columns (`Classify Char Vol` and `Curate Char Vol`).*
2.  **Curation Report & CLI Registration**:
    *   Add `"curation_diagnostics"` to the `report_type` list in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md).
    *   Define a dedicated JSON Schema section for `curation_diagnostics` in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md) including `metrics` (approval rate, character volume, and latency delay objects), `curation_rejection_mix` (rejection reason distributions), and `breakdowns` (required array, empty `[]` when group is empty).
    *   Register the `analyze-curation` subcommand in `cli.py` to invoke `CurateService`.
    *   Ensure `analyze-curation` respects the standard `--format` parameter behavior (generating either Markdown or JSON, supporting `--stdout`), outputting `CURATION_PERFORMANCE_REPORT.md` or `CURATION_PERFORMANCE_REPORT.json`.

### 4. Developer Environment & Testing Strategy

1.  **Dependency Updates**:
    *   Create `modules/analysis/requirements-dev.txt` containing `jsonschema` for schema validation testing.
2.  **JSON Schema Isolation**:
    *   Isolate official JSON schemas (or parse them directly from the markdown code blocks in [REPORT_CONTRACTS.md](file:///C:/Users/user/Documents/exopolitics/modules/analysis/docs/REPORT_CONTRACTS.md)) during testing to assert output validity and prevent drift.
3.  **Mock Schema DDL Synchronization**:
    *   Update `DDL_STATEMENTS` in `modules/analysis/tests/generate_mock_db.py` to match the production database schemas, including:
        *   `CHECK (text_processing_status IN ('completed', 'low_context', 'failed'))`
        *   `CHECK (text_processing_reason IS NULL OR text_processing_reason IN ('post_cleanup_empty', 'too_short', 'title_only', 'title_heavy', 'template_heavy', 'mostly_links', 'truncated_to_low_context', 'missing_body', 'sanitizer_exception'))`
4.  **Expanded Test Scenarios**:
    *   Write tests validating CLI outputs for `--format markdown`, `--format json`, and `--stdout`.
    *   Write tests verifying edge cases: zero records, rejection of unknown status values by the mock DDL CHECK constraint, and 5-state cohort invariants for valid states.

---

## Phase 2: Cross-Module Ingest Integration (Medium Risk)

This phase addresses the root cause of the initialization latency skew by introducing batch and attempt traceability at the ingestion layer.

### 1. Ingest Batch Traceability Schema Changes & Migrations

1.  **Fetch Attempt outcome Constraint Migration**:
    The production `fetch_attempt` table limits `outcome` to `'success'` or `'failed'` via a CHECK constraint. To support tracking active attempts, a migration must be run to alter the table structure:
    *   **Migration SQL**:
        ```sql
        PRAGMA foreign_keys=OFF;
        BEGIN IMMEDIATE;
        CREATE TABLE fetch_attempt_new (
            fetch_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetch_run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            http_status INTEGER,
            error_class TEXT CHECK (error_class IN ('network_error', 'timeout_error', 'http_error_4xx', 'http_error_5xx', 'parse_error', 'validation_error', 'persistence_error', 'unexpected_error')),
            error_detail TEXT,
            outcome TEXT NOT NULL CHECK (outcome IN ('running', 'success', 'failed')),
            new_item_count INTEGER NOT NULL DEFAULT 0,
            dedup_matched_count INTEGER NOT NULL DEFAULT 0,
            low_context_count INTEGER NOT NULL DEFAULT 0,
            sanitization_failure_count INTEGER NOT NULL DEFAULT 0,
            normalization_failure_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (fetch_run_id) REFERENCES fetch_run(fetch_run_id) ON DELETE CASCADE,
            UNIQUE(fetch_run_id, source_id)
        );
        INSERT INTO fetch_attempt_new (
            fetch_attempt_id, fetch_run_id, source_id, started_at, ended_at,
            retry_count, http_status, error_class, error_detail, outcome,
            new_item_count, dedup_matched_count, low_context_count,
            sanitization_failure_count, normalization_failure_count
        )
        SELECT
            fetch_attempt_id, fetch_run_id, source_id, started_at, ended_at,
            retry_count, http_status, error_class, error_detail, outcome,
            new_item_count, dedup_matched_count, low_context_count,
            sanitization_failure_count, normalization_failure_count
        FROM fetch_attempt;
        DROP TABLE fetch_attempt;
        ALTER TABLE fetch_attempt_new RENAME TO fetch_attempt;
        CREATE INDEX idx_fetch_attempt_run_id ON fetch_attempt(fetch_run_id);
        CREATE UNIQUE INDEX idx_fetch_attempt_run_source ON fetch_attempt(fetch_run_id, source_id);
        COMMIT;
        PRAGMA foreign_keys=ON;
        PRAGMA foreign_key_check;
        ```
    *   **Repository Interfaces**:
        *   `create_running(conn, ...)`: Creates a new `fetch_attempt` record with `outcome = 'running'`.
        *   `resolve_attempt(conn, fetch_attempt_id, outcome, ...)`: Finalizes status to `'success'` or `'failed'` and writes statistics.

2.  **Fetch Attempt Acquisition Mode**:
    *   Add a new column `acquisition_mode` on the `fetch_attempt` table with a CHECK constraint:
        ```sql
        ALTER TABLE fetch_attempt ADD COLUMN acquisition_mode TEXT NOT NULL CHECK (
            acquisition_mode IN ('initial_import', 'catchup', 'incremental', 'legacy_unknown')
        ) DEFAULT 'legacy_unknown';
        ```
3.  **Item-to-Attempt Relationship**:
    *   Add `fetch_attempt_id` as a foreign key on the `source_item` table:
        ```sql
        ALTER TABLE source_item ADD COLUMN fetch_attempt_id INTEGER REFERENCES fetch_attempt(fetch_attempt_id);
        CREATE INDEX idx_source_item_fetch_attempt_id ON source_item(fetch_attempt_id);
        ```

4.  **Acquisition Mode Decision Matrix**:
    The ingestion process determines `acquisition_mode` upon creating an attempt according to the following mapping:

    | Ingest Trigger | Operational Context | Assigned `acquisition_mode` |
    | :--- | :--- | :--- |
    | **Automatic Cron / Scheduled** | Routine incremental run. | `incremental` |
    | **Manual Command Line / UI** | Running a standard incremental fetch manually. | `incremental` |
    | **New Source Initialization** | The first fetch execution after adding an RSS source. | `initial_import` |
    | **Backfill / Catchup** | Fetching historical ranges to fill gaps (manual/scheduled). | `catchup` |
    | **Automatic Recovery Run** | Re-running a failed or interrupted fetch run. | `incremental` |
    | **Legacy Records** | Migrated records existing before acquisition tracking. | `legacy_unknown` |

### 2. Ingest Orchestrator refactoring (Write Sequence A)

To populate the `fetch_attempt_id` foreign key on items during ingestion, the Ingest Orchestrator's execution sequence is refactored:
1.  **Attempt Creation**: At the start of a fetch attempt, invoke `create_running()` to insert a `fetch_attempt` record, populating `started_at` and setting `outcome = 'running'` and `acquisition_mode` from the decision matrix. Retrieve the generated `fetch_attempt_id`.
2.  **Item Persistence**: Insert harvested feed items into `source_item` directly populating the `fetch_attempt_id` column.
3.  **Attempt Resolution**: Upon completion, invoke `resolve_attempt()` to update the `fetch_attempt` record, changing `outcome` to `'success'` or `'failed'` and populating metrics (`new_item_count`, etc.) and `ended_at`.

### 3. Steady-State SLA Reporting & Legacy Migration

1.  **Steady-State SLA Filtering**:
    *   To evaluate operational SLA, item-cohort latency queries (feed freshness, E2E, classification, curation, translation, and publish delays) must filter on `acquisition_mode = 'incremental'` by joining through `fetch_attempt`.
    *   **SQL Schema Integration**:
        ```sql
        SELECT ...
        FROM source_item si
        JOIN fetch_attempt fa ON si.fetch_attempt_id = fa.fetch_attempt_id
        WHERE fa.acquisition_mode = 'incremental'
        ```
    *   `fetch_execution_latency` will be filtered directly on the `fetch_attempt` table where `acquisition_mode = 'incremental'`.
    *   Fetch success and failure rates must count only terminal attempts where `outcome IN ('success', 'failed')`; active `running` attempts are excluded from their denominators and numerators.
2.  **Legacy Data Treatment (No Provenance vs. Unknown Mode)**:
    *   **No Provenance (Unattributed)**: Legacy `source_item` rows existing before the migration will have `fetch_attempt_id = NULL`. These are counted in reports as `excluded_legacy_items` using `si.fetch_attempt_id IS NULL`. They are completely excluded from SLA calculations.
    *   **Unknown Ingestion Mode**: Any migrated `fetch_attempt` records existing before the migration will default to `acquisition_mode = 'legacy_unknown'`. Items linked to these attempts are also excluded from SLA calculations.
3.  **Warning Banner Removal Validation**:
    The warning banner will only be removed when:
    *   All latency queries join `fetch_attempt` and filter on `fa.acquisition_mode = 'incremental'`.
    *   Legacy data (both `NULL` `fetch_attempt_id` and `'legacy_unknown'` modes) is explicitly handled and excluded from SLA.
    *   Unit and integration tests covering the new orchestrator lifecycle (Attempt transition, FK consistency), database migration DDL, and exclusion of running attempts from fetch success/failure rate denominators pass successfully.
