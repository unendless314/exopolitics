# Curate Module

**Document version:** v1.4  
**Updated:** 2026-06-16  
**Status:** Planning & Active rewrite draft

---

## 1. Module Positioning

`curate` is the third executable module in the processing pipeline:

`ingest -> classify -> curate -> edit (when needed) -> publish -> site`

The module reads `classification_result` records that have been classified as `core` or `adjacent`, evaluates their content via an LLM-driven curation prompt, and persists the curation decisions, editor handoff briefs (required for all non-discarded items), and immediately publishable summary outputs (required only for approved items) in the canonical database.

### Core Architectural Separation (Post-Rewrite V2)
In this revised MVP rewrite:
* **Workflow Decisions:** `curate` owns the downstream workflow state through the `curation_decision` contract.
* **Separation of Outputs:** Conceptually and structurally, `curate` separates workflow decisions (`curation_decision`), future human/editor handoff instructions (`editor_brief`), and the public-facing content product (`curation_output`).
* **Boundary Rules:** 
  * `curate` must **not** write to `edit_draft` or own site-owned draft logic. Instead, it generates an `editor_brief` to decouple `curate` from the future `edit` module.
  * The direct consumers of `curation_output` are downstream publishing and export layers (such as a temporary, external `publish mock` validation script). The `curate` module does **not** render pages or own site rendering logic.
  * `curate` does **not** dictate global model prompt design. It executes using a locally managed config that satisfies a strict curation prompt contract.
  * **Bypass Complex Full-Text Reconstruction:** To preserve MVP speed, `curate` operates strictly on the `source_item_text.sanitized_text` provided by the `ingest` module. On-demand scraping and raw payload reconstruction are deferred.

---

## 2. Key Responsibilities

1. **Pending Queue Selection:** Query uncurated items (or failed items eligible for automatic retry) that have a classification result of `core` or `adjacent`.
2. **LLM Triage & Evaluation:** Evaluate items by executing a local runner that satisfies the prompt contract to determine eligibility for publication based on content quality, sensationalism, and noise.
3. **Structured Handoff & Output Generation:** Generate structured outputs representing the curation decision, editorial guidance, and clean normalized text representation.
4. **Link-Only Publishing Support (`publish_link`):** Allow announcements or short-context items to bypass bullet-point summary generation, providing display title normalization, time metadata, a short summary paragraph (excerpt), and source attribution framing to save token costs.
5. **Conditional Persistence:** Persist curation decisions for all items, editor briefs for non-discarded items (`publish_link`, `publish_summary`, `edit_rewrite`), and publishable outputs only for approved items (`publish_link`, `publish_summary`).

---

## 3. Document Map

* [DATA_CONTRACT.md](file:///C:/Users/user/documents/exopolitics/modules/curate/docs/DATA_CONTRACT.md)  
  Defines the database schema for `curation_decision`, `editor_brief`, and `curation_output`, including table indexes, foreign keys, and retry count tracking.
* [STATE_TRANSITIONS.md](file:///C:/Users/user/documents/exopolitics/modules/curate/docs/STATE_TRANSITIONS.md)  
  Defines curation workflow states, retry loops, transition rules, and re-curation data cleanup side-effects.
* [CURATION_POLICY.md](file:///C:/Users/user/documents/exopolitics/modules/curate/docs/CURATION_POLICY.md)  
  Defines editorial criteria for triage (approval vs. rejection), noise filtering rules, and downstream routing guidelines.
* [PROMPT_CONTRACT.md](file:///C:/Users/user/documents/exopolitics/modules/curate/docs/PROMPT_CONTRACT.md)  
  Defines the model instructions, prompt variables, and the single-pass JSON output schema (strictly excluding runner-side failed states).
* [EXECUTION_POLICY.md](file:///C:/Users/user/documents/exopolitics/modules/curate/docs/EXECUTION_POLICY.md)  
  Defines the execution orchestration including batch processing, rate-limit handlers, database transaction blocks, and preview options.
* [IMPLEMENTATION_PLAN.md](file:///C:/Users/user/documents/exopolitics/modules/curate/docs/IMPLEMENTATION_PLAN.md)  
  Outlines the project roadmap broken down into epics, migration scripts, testing strategies, and MVP constraints.

---

## 4. Configuration Schema Specs

The `curate` module uses typed YAML configurations parsed into Pydantic models at runtime.

### 4.1 `model_settings.yaml` Schema
| Field Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `active_provider` | `string` | **Yes** | Identifier of the active LLM provider in the `providers` map. |
| `active_prompt_template` | `string` | **Yes** | Identifier of the active template in `prompt_templates.yaml`. |
| `request_defaults` | `object` | **Yes** | Default settings: `temperature` (float, 0.0-2.0, default 0.2), `top_p` (float, 0.0-1.0, default 0.95), and `max_output_tokens` (integer, default 2048). |
| `execution_policy` | `object` | **Yes** | Runner settings: `batch_size` (int, default 20), `max_concurrent_requests` (int, default 3), `rate_limit_per_minute` (int, default 60), `request_timeout_seconds` (float, default 60.0), `retry_attempts` (int, default 3), and `backoff_factor` (float, default 2.0). |
| `providers` | `map` | **Yes** | Map of provider key to settings: `api_type` (openai/openai_compatible), `api_key_env` (env var name), `model_name` (string), `supports_structured_output` (bool, default false), `api_base` (optional URL). |

### 4.2 `prompt_templates.yaml` Schema
| Field Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `templates` | `map` | **Yes** | Map of template key to configuration. Each template requires `version` (string), `description` (optional string), `system_instruction` (string), and `user_prompt_template` (string). |

---

## 5. Pre-production Schema Reset Policy

Since the system is in pre-production and data can be rebuilt from upstream sources (as per [IMPLEMENTATION_ROADMAP.md](file:///C:/Users/user/Documents/exopolitics/docs/IMPLEMENTATION_ROADMAP.md)), we explicitly choose to update the initial DDL script [v001_initial_curate_tables.sql](file:///C:/Users/user/Documents/exopolitics/modules/curate/src/migrations/v001_initial_curate_tables.sql) directly.
* This refactoring is a **pre-production schema reset**.
* We do **not** provide incremental schema migration scripts (like `v002`) for existing local databases.
* To apply this refactoring locally, you must either drop/recreate the curation tables (`curation_decision`, `editor_brief`, `curation_output`) and remove the `'v001_initial_curate_tables.sql'` row from `schema_migrations`, or delete and rebuild the `canonical.db` database entirely.

---

## 6. Minimal CLI Usage

Validate configurations:

```text
python -m modules.curate.src.cli validate
```

Run curate database migrations:

```text
python -m modules.curate.src.cli migrate --db-path data/canonical.db
```

Preview pending items and prompts without saving or calling the model:

```text
python -m modules.curate.src.cli run --db-path data/canonical.db --preview-prompts --batch-size 3
```

Run a live curation batch:

```text
python -m modules.curate.src.cli run --db-path data/canonical.db --batch-size 20
```

Curate a specific item (forced re-run of a completed item requires `--force`):

```text
python -m modules.curate.src.cli run --db-path data/canonical.db --source-item-id 123 --force
```

Check curation queue status and stats:

```text
python -m modules.curate.src.cli status --db-path data/canonical.db
```

### 6.1 Status Output Definition
The `status` command queries `data/canonical.db` and must print a standardized, formatted summary of the curation queue:
* **`pending`**: Count of items eligible for curation (source_item status 'ingested', classification topic 'core'/'adjacent', and no curation decision OR status 'failed' with retry_count < 3).
* **`locked` (Failed permanently)**: Count of items that failed curation and reached `retry_count >= 3`.
* **`approved`**: Count of approved items, showing a breakdown:
  * `publish_link` (Bookmark Mode)
  * `publish_summary` (Full Summary Mode)
* **`withdrawn`**: Count of manually withdrawn items, showing a breakdown:
  * `publish_link` (Withdrawn Bookmark Mode)
  * `publish_summary` (Withdrawn Full Summary Mode)
* **`rejected`**: Count of rejected items, showing a breakdown:
  * `edit_rewrite` (Soft Reject Mode)
  * `reject_discard` (Hard Reject Mode)
* **`total_failed_runs`**: Count of items currently in a failed state (both transient failed and permanently locked).
