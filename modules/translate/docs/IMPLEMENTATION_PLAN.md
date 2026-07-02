# Translate Implementation Plan

**Document version:** v1.3  
**Updated:** 2026-07-02  
**Status:** Locked Contract  

> [!IMPORTANT]
> **Co-location Disclaimer**: The `approved_content_record` table represents a shared canonical handoff capability (not owned solely by the `translate` module). For implementation simplicity, its schema migrations and assembly helper scripts are temporarily co-located under `modules/translate/`. This co-location does not alter the module boundaries defined in [MODULE_BOUNDARIES.md](file:///C:/Users/user/Documents/exopolitics/docs/MODULE_BOUNDARIES.md); `translate` remains a pure downstream consumer of this handoff table.

---

## 1. Implementation Steps

The development of the `translate` module is divided into the following epics and must adhere to the contracts defined in [PROMPT_CONTRACT.md](./PROMPT_CONTRACT.md), [EXECUTION_POLICY.md](./EXECUTION_POLICY.md), [STATE_TRANSITIONS.md](./STATE_TRANSITIONS.md), and [TRANSLATION_POLICY.md](./TRANSLATION_POLICY.md).

### Epic 1: Database Migration & Setup
- Create directory structure for `modules/translate/` (`src/`, `config/`, `docs/`, `tests/`).
- Create `modules/translate/src/migrations/v001_initial_translate_tables.sql` containing the DDL for both the shared `approved_content_record` table and the `translation_output` table.
- Write Python database utility (e.g., proposed as `modules/translate/src/database.py`) to run initial schema migrations.
- Ensure integration tests verify that tables are successfully constructed in `data/canonical.db`.

### Epic 2: Shared Handoff Assembly & Core Translation Logic
- **Shared Handoff Assembly Utility (Co-located)**:
  - This repository co-locates a shared handoff assembler utility under `modules/translate/src/approved_content_record.py` (or `handoff_repository.py`) for delivery simplicity.
  - This assembler is *not* owned by the `translate` module; it operates as a separate upstream step. The `translate` runner consumes `approved_content_record` only after this upstream assembly step is executed and completed.
  - The assembler reads finalized upstream curation approvals (from `curation_output`/`editor_brief`) and finalized edit drafts, splices the Markdown content body, normalizes line endings, computes the SHA-256 `content_fingerprint` of the normalized title/body payload, and writes them to the `approved_content_record` table.
  - **Language Normalization Policy**: Under the current system policy, all curate-originated mother-drafts must be materialized with `content_language_code = 'en'`, bypassing the use of `classification_result.primary_language_code` as a fallback.
  - The assembler must remain translation-agnostic so it can later be moved into a shared location with minimal refactoring if more modules depend on the same handoff contract.
  - Upstream refresh logic should be delta-oriented: detect rows whose upstream finalized state changed since the last handoff materialization and only recompute those fingerprints instead of rebuilding the entire table each run.
  - For the MVP, if the effective upstream finalized source does not provide a dedicated `finalized_at`-style field, use that source row's `updated_at` as the pre-screen freshness signal.
  - The delta algorithm should be: compare upstream effective `updated_at` to `approved_content_record.updated_at`, re-assemble only later candidates, then recompute the fingerprint and persist only if the handoff payload or approval metadata actually changed.
- **Fingerprinting & Invalidation Logic (Translate Module)**:
  - Implement fingerprint alignment in the runner (retrieving the canonical `content_fingerprint` from `approved_content_record`).
  - Implement source retrieval and change detection logic following [EXECUTION_POLICY.md](./EXECUTION_POLICY.md):
    - Query newly approved or updated items from `approved_content_record`.
    - Compare the upstream stored `content_fingerprint` and model/prompt config against existing cached translations without recomputing hashes from raw content.
    - Insert or mark matching records as `stale` or `pending` as appropriate.
  - Read `display_title` and `content_body` directly from `approved_content_record` (the mother-draft is pre-spliced, so no local splicing is required).

### Epic 3: LLM Integration & Service
- Integrate LLM client utilizing configured prompt templates for translation in accordance with [PROMPT_CONTRACT.md](./PROMPT_CONTRACT.md).
- Write prompt files or configurations under `modules/translate/config/`.
- Handle prompt formatting, API execution, validation of model responses, and error state transitions (mapping API failures/timeouts to `failed` and managing `retry_count`).
- Implement rate limiting, concurrency controls (via semaphore), and exponential backoff as specified in [EXECUTION_POLICY.md](./EXECUTION_POLICY.md).

### Epic 4: CLI Interface
- Implement the module entry CLI command runner (e.g., proposed as `modules/translate/src/cli.py`) to support administration commands such as:
  - `migrate`: Runs the initial schema setup.
  - `run`: Processes pending translations, calls LLM, updates cache records, and transitions states.
  - `status`: Displays translation statistics (count of completed, pending, stale, failed, and locked translations by language).

---

## 2. Testing Guidelines

### Unit Tests
Create unit tests under `modules/translate/tests/` to verify:
1. **Co-located Shared Handoff Utility**: Verify that the co-located upstream handoff assembler correctly compiles finalized curation/edit states into `approved_content_record` with accurate fingerprints.
2. **Fingerprint Validation**: Verify the runner accurately detects update drift by comparing cached records against changes in `approved_content_record.content_fingerprint`.
3. **Invalidation Scenarios**: Verify that editing a mother-draft updates the fingerprint and moves the translation record state to `stale`.
4. **Configuration Changes**: Verify that altering the model or prompt configuration version forces cached rows into the `stale` state.
5. **Mock LLM Execution**: Mock API responses to test successful transitions to `completed` and failure paths to `failed` (verifying `retry_count` incrementing and locking at `retry_attempts` as defined in `config/model_settings.yaml`).

### Integration Tests
- Run full CLI command suite on sandbox/mock SQLite database records.
- Verify DDL migrations build successfully.
- Verify `cli run` correctly inserts the translated rows with unique composite keys into SQLite.
