# Translate Implementation Plan

**Document version:** v1.0  
**Updated:** 2026-06-18  
**Status:** Active planning & design

> [!NOTE]
> Detailed code-level properties (e.g., target language lists, database file patterns, and CLI command flags) documented here represent **current design proposals and working assumptions** to guide the MVP implementation, rather than locked top-level system contracts.

---

## 1. Implementation Steps

The development of the `translate` module is divided into the following epics:

### Epic 1: Database Migration & Setup
- Create directory structure for `modules/translate/` (`src/`, `config/`, `docs/`, `tests/`).
- Create `modules/translate/src/migrations/v001_initial_translate_tables.sql` containing the DDL for `translation_output`.
- Write Python database utility (e.g., proposed as `modules/translate/src/database.py`) to run initial schema migrations.
- Ensure integration tests verify that tables are successfully constructed in `data/canonical.db`.

### Epic 2: Core Translation & Fingerprinting Logic
- Implement fingerprint alignment in the runner (retrieving the canonical `content_fingerprint` from `approved_content_record`).
- Implement source retrieval and change detection logic:
  - Query newly approved or updated items from `approved_content_record`.
  - Compare the upstream `content_fingerprint` and model/prompt config against existing cached translations.
  - Insert or mark matching records as `stale` or `pending` as appropriate.
- Splicing function to normalize upstream content drafts before translating.

### Epic 3: LLM Integration & Service
- Integrate LLM client utilizing configured prompt templates for translation.
- Write prompt files or configurations under `modules/translate/config/`.
- Handle prompt formatting, API execution, validation of model responses, and error state transitions (e.g. mapping API failures/timeouts to `failed`).
- Implement rate limiting, concurrency controls, and stagger execution to prevent LLM API exhaustions.

### Epic 4: CLI Interface
- Implement the module entry CLI command runner (e.g., proposed as `modules/translate/src/cli.py`) to support administration commands such as:
  - `migrate`: Runs the initial schema setup.
  - `run`: Processes pending translations, calls LLM, updates cache records, and transitions states.
  - `status`: Displays translation statistics (count of completed, pending, stale, failed translations by language).

---

## 2. Testing Guidelines

### Unit Tests
Create unit tests under `modules/translate/tests/` to verify:
1. **Fingerprint Validation**: Verify the runner accurately detects update drift by comparing cached records against changes in `approved_content_record.content_fingerprint`.
2. **Invalidation Scenarios**: Verify that editing a mother-draft updates the fingerprint and moves the translation record state to `stale`.
3. **Configuration Changes**: Verify that altering the model or prompt configuration version forces cached rows into the `stale` state.
4. **Mock LLM Execution**: Mock API responses to test successful transitions to `completed` and failure paths to `failed`.

### Integration Tests
- Run full CLI command suite on sandbox/mock SQLite database records.
- Verify DDL migrations build successfully.
- Verify `cli run` correctly inserts the translated rows with unique composite keys into SQLite.
