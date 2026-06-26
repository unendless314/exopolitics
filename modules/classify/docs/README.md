# Classify Module

**Document version:** v3.1  
**Updated:** 2026-06-11  
**Status:** Planning & Active rewrite draft

---

## 1. Module Positioning

`classify` is the second executable module in the processing pipeline:

`ingest -> classify -> curate -> edit (when needed) -> publish -> site`

The module reads `source_item` rows that have been successfully ingested but lack classification, evaluates their content, and writes the classification results and descriptive signals to the canonical database.

### Core Architectural Separation (Post-Rewrite)
In this rewrite, `classify` is decoupled from text cleanup and raw data parsing:
* **Inputs:** `classify` strictly consumes the **sanitized working text** (`source_item_text` table) produced by the `ingest` module. It does not parse raw HTML, RSS summaries, or raw feeds.
* **Low-Context Handling (Current Default MVP Strategy):** To save API costs and prevent hallucinations, `classify` by default bypasses the LLM for items already flagged as low-context by `ingest` (using `source_item_text.is_low_context`), writing a deterministic `unknown` classification record with all descriptors set to `NULL` instead. This is a configurable strategy rather than a hardbound architectural limitation.

### Downstream Consumption
* **Downstream Consumer:** The direct consumer of classification results is the **`curate`** module. 
* **Boundary Rule:** The **`publish`** module must **not** read `classification_result` directly. It only exports items that have been explicitly approved by the **`curate`** module.

---

## 2. Key Responsibilities

1. **Pending Queue Selection:** Identify unclassified items using a joined query between `source_item` and `source_item_text` where no `classification_result` exists.
2. **Deterministic Pre-Checks (MVP Default):** Automatically mark items with `is_low_context = 1` as `unknown` (and descriptors as `NULL`) to save LLM cost.
3. **LLM Classification:** Submit sanitized text to the LLM to categorize items into `core`, `adjacent`, `irrelevant`, or `unknown`.
4. **Descriptive Tagging:** Generate structured descriptive signals (content density, text quality, language, and official involvement) and an optional experimental JSON metadata signal to support downstream curation triage.
5. **Persistence:** Write structured classification outcomes back to `classification_result` in the canonical database.

---

## 3. Document Map

* [DATA_CONTRACT.md](file:///C:/Users/user/documents/exopolitics/modules/classify/docs/DATA_CONTRACT.md)  
  Defines the `classification_result` table schema, index strategy, and pending item query semantics.
* [CLASSIFICATION_POLICY.md](file:///C:/Users/user/documents/exopolitics/modules/classify/docs/CLASSIFICATION_POLICY.md)  
  Defines the classification categories (`core`, `adjacent`, `irrelevant`, `unknown`) and policies for low-context detection and descriptive tagging.
* [PROMPT_CONTRACT.md](file:///C:/Users/user/documents/exopolitics/modules/classify/docs/PROMPT_CONTRACT.md)  
  Defines model instruction templates, prompt variables, and JSON response schema guidelines.
* [EXECUTION_POLICY.md](file:///C:/Users/user/documents/exopolitics/modules/classify/docs/EXECUTION_POLICY.md)  
  Defines execution controls including batch size, rate limits, retry policies, and transaction boundaries.
* [IMPLEMENTATION_PLAN.md](file:///C:/Users/user/documents/exopolitics/modules/classify/docs/IMPLEMENTATION_PLAN.md)  
  Defines the development plan broken down into epics, stories, and tasks, including contract requirements and MVP choices.

## 4. Config Map

* `config/prompt_templates.yaml`  
  Stores the active prompt template registry used by the classify module.
* `config/model_settings.yaml`  
  Stores provider selection, request defaults, execution defaults, and deterministic classification metadata.

---

## 5. Minimal CLI Usage

Validate configuration:

```text
python -m modules.classify.src.cli validate
```

Run classify migrations:

```text
python -m modules.classify.src.cli migrate --db-path data/canonical.db
```

Preview pending prompts without calling the model:

```text
python -m modules.classify.src.cli run --db-path data/canonical.db --preview-prompts --batch-size 2
```

Run one live batch:

```text
python -m modules.classify.src.cli run --db-path data/canonical.db --batch-size 20
```
