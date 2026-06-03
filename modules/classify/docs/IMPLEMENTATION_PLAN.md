# Classify Module Implementation Plan

**Document version:** v1.0  
**Updated:** 2026-06-03  
**Status:** Active Draft  

This implementation plan serves as the blueprint for developing the `classify` module MVP. It defines the codebase layout, development sequence, and the verification test matrix.

---

## 1. Codebase Layout & File Architecture

The implementation will be structured under `modules/classify/` as follows:

```text
modules/classify/
├── config/
│   ├── model_settings.yaml            # Model & provider configuration
│   └── prompt_templates.yaml          # Prompt templates registry
├── docs/
│   ├── CLASSIFICATION_POLICY.md       # Category definitions
│   ├── DATA_CONTRACT.md               # DB schema & queries contract
│   ├── EXECUTION_POLICY.md            # Concurrency & Parser contract
│   ├── PROMPT_CONTRACT.md             # LLM input/output contract
│   ├── IMPLEMENTATION_PLAN.md         # This plan
│   └── README.md                      # Overview
├── src/
│   ├── __init__.py
│   ├── cli.py                         # Click/Argparse interface (classify run)
│   ├── config.py                      # Configurations and settings loader
│   ├── prompt_loader.py               # Template parser and resolver
│   ├── repository.py                  # Database reads/writes (SQLite queries)
│   ├── classifier.py                  # Orchestrator (loop, check, LLM client)
│   └── migrations/
│       └── v002_initial_classify_tables.sql
└── tests/
    ├── __init__.py
    ├── conftest.py                    # Pytest DB and API mocks
    ├── test_config.py                 # Settings loading tests
    ├── test_repository.py             # Database read/write unit tests
    └── test_classifier.py             # Core pipeline execution & retry tests
```

---

## 2. Development Sequence

We will execute the development in 5 steps:

### Step 1: Config & Prompt Loaders (`config.py`, `prompt_loader.py`)
* Implement typed data classes or configuration models matching `model_settings.yaml`.
* Implement prompt loader capable of extracting named templates from `prompt_templates.yaml` and rendering `{title}` and `{summary}` variables.

### Step 2: Database Persistence (`repository.py`)
* Write queries to select unclassified `source_item` rows using the Left Join specified in `DATA_CONTRACT.md`.
* Write transactional save queries for inserting/updating rows in `classification_result`.

### Step 3: Classifier Orchestration (`classifier.py`)
* Write client wrapper to interact with OpenAI API (or other providers configured in `model_settings.yaml`).
* Build local pre-check validation (length character check).
* Build JSON parsing parser and schema validation logic conforming to the `EXECUTION_POLICY.md` parser contract.
* Build parallel execution loop (`asyncio.gather` up to `max_concurrent_requests`) with retry/backoff throttling.

### Step 4: CLI Interface (`cli.py`)
* Hook up the Click CLI command `classify run` that triggers the orchestrator batch execution.
* Ensure progress outputs are streamed properly to `stderr`.

### Step 5: Test Execution and E2E validation
* Verify execution using unit and integration tests.

---

## 3. Minimal Test Matrix

Before finalizing a PR, the following test scenarios must be verified under `modules/classify/tests/`:

| Test ID | Scenario | Input Context | Expected Behavior / Output |
| :--- | :--- | :--- | :--- |
| **TC-01** | Deterministic Low-Context | Title + Summary < `min_context_characters` | Bypasses LLM. Instantly writes `topic_class = 'unknown'`, `classification_confidence = NULL`, a reason that explicitly indicates insufficient feed context and may include the measured length and threshold, `model_name = 'deterministic-low-context'`, `prompt_version = 'rule_v1'`. |
| **TC-02** | Successful Core Classification | Standard item; LLM returns valid `'core'` JSON | Writes `classification_result` row with appropriate attributes, `classified_at` timestamp, `model_name` and `prompt_version` from config. |
| **TC-03** | LLM-Produced Unknown | Standard item; LLM returns valid `'unknown'` JSON | Writes `classification_result` row with `topic_class = 'unknown'`. |
| **TC-04** | Transient Malformed Output | Standard item; LLM first returns malformed JSON, then valid JSON | Parser rejects first attempt, triggers retry loop, succeeds on second attempt, and persists the correct row. |
| **TC-05** | Persistent Failure (Retries Exhausted) | Standard item; LLM consistently times out or returns bad JSON | Retries up to `retry_attempts`, logs the failure type to error logs, writes **no** row for this item, and successfully continues with the rest of the batch. |
| **TC-06** | Queue Selection & De-duplication | Database contains 1 classified item and 1 unclassified item | Pending item query select only the unclassified item. |
