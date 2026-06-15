# Curate Module Implementation Plan

**Document version:** v1.4  
**Updated:** 2026-06-15  
**Status:** Planning & Active rewrite draft

---

## 1. Project Directory Structure

The initial module structure will reside under `modules/curate/`:

```text
modules/curate/
├── config/
│   ├── model_settings.yaml
│   └── prompt_templates.yaml
├── docs/
│   ├── DATA_CONTRACT.md
│   ├── EXECUTION_POLICY.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── PROMPT_CONTRACT.md
│   ├── README.md
│   ├── CURATION_POLICY.md
│   └── STATE_TRANSITIONS.md
├── src/
│   ├── migrations/
│   │   └── v001_initial_curate_tables.sql
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── database.py
│   └── orchestrator.py
└── tests/
    ├── __init__.py
    ├── test_database.py
    └── test_orchestrator.py
```

---

## 2. Implementation Epics

The implementation is divided into four main epics:

### Epic 1: Database Schema & Migration
* **Goal:** Create the durable curation tables and set up the repository pattern.
* **Tasks:**
  * Write `v001_initial_curate_tables.sql` DDL migration script incorporating the `retry_count` column and the `CHECK` constraint validating `downstream_action` nullability against `curate_status`.
  * Create `src/database.py` containing:
    * `CurationRepository`: Methods for `get_pending_items()`, `upsert_curation_decision()`, `upsert_editor_brief()`, and `upsert_curation_output()`.
    * Ensure the repository updates/resets `retry_count` on successful model outputs and writes `downstream_action = NULL` on runner failures.
    * Integration with the database runner (`run_migrations`).

### Epic 2: Core Orchestrator & LLM Client
* **Goal:** Implement the LLM call, structured output parser, and execution logic.
* **Tasks:**
  * Create `config/prompt_templates.yaml` and `config/model_settings.yaml`.
  * Create `src/config.py` to parse and load these settings.
  * Create `src/orchestrator.py` containing:
    * Prompt builder.
    * LLM client wrapper (calling OpenAI/compatible API with schema enforcement).
    * Exception handlers that catch model schema mismatch or rate-limits, incrementing `retry_count` and persisting a `'failed'` status with `downstream_action = None`.
    * Batch loop logic that locks transactions, runs the curation process, and persists results.

### Epic 3: CLI Interface
* **Goal:** Expose commands to run migrations, trigger batch runs, preview prompts, and view stats.
* **Tasks:**
  * Create `src/cli.py` using `click` (matching `classify/src/cli.py` style).
  * Expose commands:
    * `migrate`: Apply the schema DDL.
    * `run`: Run automated curation with `--batch-size` and `--preview-prompts` options.
    * `status`: Print counts of pending, approved, rejected, and failed items.

### Epic 4: Verification & Testing
* **Goal:** Ensure unit test coverage and validate the pipeline end-to-end.
* **Tasks:**
  * Write `tests/test_database.py` using a mock SQLite database to verify table constraints, cascading deletes, repository retry logic, and the `CHECK` constraint for `downstream_action`.
  * Write `tests/test_orchestrator.py` mocking the LLM API to verify correct routing, retry selection, and parsing of output JSON.

---

## 3. Testing Strategy

* **Local Mock Databases:** Run tests against a temporary `:memory:` or local SQLite mock DB to ensure cascading deletes (`ON DELETE CASCADE`) and unique constraints work.
* **State Transition Cleanup Validation:** Unit tests in `tests/test_database.py` must verify that transitioning between downstream actions (e.g., approved -> reject_discard or approved -> edit_rewrite) correctly purges orphaned database rows in `editor_brief` and `curation_output` according to `STATE_TRANSITIONS.md`.
* **LLM Mocking:** Mock the LLM client call response during orchestrator tests. Ensure the parser handles:
  * Missing bullet points on `publish_link` (bullets should map to `NULL` in the DB, while `summary_short` is properly populated as the excerpt).
  * Incomplete JSON responses (must catch exception, write `failed` status to DB, increment `retry_count` by `1`, and verify `downstream_action` is written as `NULL`).
  * Automatic retries of items with `status = 'failed'` and `retry_count < 3`.
  * Proper exclusion of `additional_signals` from upstream data selection.
  * Validation matrix enforcement (raises ValidationError on invalid schema state combination).

---

## 4. Key Architectural Assumptions & Decisions

During planning, the following decisions were resolved:
1. **Runner-Generated Failed State:** The status `'failed'` is purely a runner-side persistence state. The model schema contract only allows it to return `'approved'` or `'rejected'`.
2. **Downstream Action Nullability & Routing:** To keep the database contract clean, `downstream_action` is nullable. It must be `NULL` for `curate_status = 'failed'`, and `NOT NULL` for approved/rejected records. Valid values are `'publish_link'`, `'publish_summary'`, `'edit_rewrite'`, and `'reject_discard'`. This is enforced at the database level via a `CHECK` constraint.
3. **Auto-Retry Limit:** Failed items will auto-retry in the queue up to 3 times before locking, preventing infinite loop token waste.
4. **No Mock Renderers inside Curation:** Downstream rendering is completely out of scope for the `curate` module. Any temporary `publish mock` scripts are documented purely as external validation consumers.
5. **Summary Short Constraint:** `summary_short` is defined as `NOT NULL` across all presentation outputs. For `publish_link` items, the curator is explicitly instructed to generate a single-sentence excerpt to satisfy this constraint.
6. **Conditional Table Row Creation & Validation Matrix:** To optimize token utilization and database semantics, `editor_brief` is required and created if `downstream_action` is `'publish_link'`, `'publish_summary'`, or `'edit_rewrite'`. `curation_output` is required and created if `downstream_action` is `'publish_link'` or `'publish_summary'`. Otherwise, they are omitted. Both are defined as nullable objects in the LLM JSON response schema, and validated programmatically using the Curation Result Validation Matrix.
7. **SQLite Concurrency and File Locking:** To coordinate execution safely on SQLite, a file-level exclusive lock (`data/curate_runner.lock`) is held by the active orchestrator process. Internal parallelism is handled using a semaphore, and write queries are serialized using an asynchronous lock to keep transactions brief and avoid SQLite write collisions.
