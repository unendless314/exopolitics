# Open Questions & Meeting Minutes (Classify Module)

This document serves as the shared discussion record between the **User (Product Owner)** and the **AI Agents (Antigravity & Peer AIs)** regarding the `classify` module. We will co-maintain this file to record technical discussion topics, align on specifications, and document consensuses.

> [!IMPORTANT]
> **Consensus Rule:** This file is a discussion and decision log. Implementation should follow the formal specifications in `README.md`, `DATA_CONTRACT.md`, `CLASSIFICATION_PROMPT.md`, and `BATCH_POLICY.md`.

---

## 1. Active Discussion Topics

### Topic 1.1: Classification Persistence Model (1-to-1 vs. 1-to-Many)
* **Question:** Should a `source_item` be linked to exactly one `classification_result` (1-to-1), or should we support multiple historical classification runs (1-to-many)?
* **Perspectives:**
  * **Antigravity's View:** 
    * A **1-to-1** relationship (with `source_item_id UNIQUE` constraint on the `classification_result` table) is simpler for SQLite MVP queries. It avoids complex subqueries to find the "latest classification" and keeps the database footprint small.
    * A **1-to-many** relationship allows auditing model drift (e.g., comparing `gemini-1.5-flash` performance against a newer model or prompt iteration over the same data), which is highly valuable for a production LLM system.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** Adopt the **1-to-1** relationship. ENFORCE the `source_item_id UNIQUE` constraint in the database schema. If an item is reclassified, it overwrites the existing record. History is not preserved to conserve resources and minimize API costs.
* **Status:** `[RESOLVED]`

---

### Topic 1.2: Default LLM Model & Failure Fallback Strategy
* **Question:** What model should we standardize on, and what is our fallback strategy if the LLM output is malformed or the API encounters a rate/service failure?
* **Perspectives:**
  * **Antigravity's View:**
    * Recommend standardizing on `gemini-1.5-flash` due to its high speed, low cost, and native support for Structured Outputs (JSON schema enforcement).
    * If the response is unparseable (or the API fails after 3 retries), the item should be left without a `classification_result` record (marked as pending/failed to be retried in the next cron run). If it repeatedly fails (e.g., due to content filter blocks), we should log a `last_error_class` like `'unexpected_error'` or `'content_filtered'`, and let the `review` module handle it during manual triage, rather than assigning an arbitrary default category.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** 
    * **Model Choice:** Standardize on `gpt-5.4-mini` (OpenAI cost-effective entry tier) for the initial MVP. For future runs, evaluate transitions to even more cost-competitive providers like MiniMax M3 series (`minimax-m3-series`) or DeepSeek V4 Flash (`deepseek-v4-flash`).
    * **Fallback Policy:** If classification fails (e.g., API errors or unparseable outputs after retries), do not assign a default category. Leave the item unclassified to be triaged or retried in the next cycle, or let the `review` module handle it.
* **Status:** `[RESOLVED]`

---

### Topic 1.3: Content Enrichment Trigger Policy
* **Question:** In some feeds, the RSS title and summary are extremely short (low context). When and how should the system trigger page-level scraping/retrieval to provide richer context for LLM classification?
* **Perspectives:**
  * **Antigravity's View:**
    * Following `docs/MODULE_BOUNDARIES.md`, page-level retrieval is a shared capability candidate and is **not** an MVP feature of `ingest`.
    * For `classify`, we should first run a dry check: if the combined title and summary is less than a certain character threshold (e.g., 100 characters), or if the LLM returns `irrelevant` with very low confidence due to "insufficient info", the pipeline should flag it for optional enrichment. However, in Stage 2 (MVP), we should default to classifying based purely on feed metadata, and fallback to human triage if unsure.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** Agree with the policy. If the combined title and summary length falls below a configurable threshold, the item is marked as `'unknown'`. The threshold must not be hardcoded; it will be defined in `model_settings.yaml` (e.g., as `min_context_characters`). In the current MVP, `unknown` is sufficient and no separate `classification_status` field is added.
* **Status:** `[RESOLVED]`

---

### Topic 1.4: Triage SLA and Agent-Assisted Triage for Queue Overflows (Deferred to Review Module)
* **Question:** How do we define the queue SLA limits for `classified` items, and how should Agent Triage behave when the queue overflows?
* **Perspectives:**
  * **Antigravity's View:**
    * We should define a time window (e.g., 48 hours). Any item that remains in the classification/review queue longer than this SLA is flagged.
    * When overflow occurs, an automated Agent Triage can auto-reject items labeled as `irrelevant` with >95% confidence, but any such automated action must log a clear audit trail in the `review_decision` table (stating `actor_type = 'agent'`, `actor_id = 'triage-agent'`, `confidence = 0.98`, `reason = 'Auto-rejected due to SLA timeout'`).
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** Deferred. This logic belongs strictly to the **`review`** module's boundary rules. The `classify` module's responsibility ends as soon as it writes classification results to the database. Queue management and automated triage policies will be designed in Stage 3 (`review`).
* **Status:** `[DEFERRED]`

---

### Topic 1.5: API Call Slicing (Single Item vs. Multi-Item Batching per API Call)
* **Question:** Should each API call process exactly one `source_item`, or should we batch multiple items (e.g., 10 to 50 items) inside a single prompt?
* **Perspectives:**
  * **Antigravity's View:**
    * **Single Item (1 item per API call):**
      * *Pros:* Highest classification accuracy; supports native LLM Structured Outputs / JSON Schema validation seamlessly; a single item's parsing failure or context block does not disrupt other items; simple logic.
      * *Cons:* Higher latency if run sequentially (though mitigated by `asyncio` parallel execution); slightly higher API call count and token overhead (system instruction tokens are repeated).
    * **Multi-Item Batch (e.g., 10-50 items per API call):**
      * *Pros:* Very cost-efficient (system prompt tokens shared); lower total latency; avoids API rate limits easily.
      * *Cons:* Vulnerable to output formatting/JSON errors (if the model misses one key, the whole batch JSON may fail parsing); higher risk of "attention dilution" (LLM hallucinating or mixing details between items); output token limits (max output tokens of 8192 might truncate if LLM writes detailed reasons for 50 items).
    * **Future-Proofing Design (Decoupled Batch Interface):**
      * To ensure we do not block future upgrades to high-volume batching, we should design the classifier code interface (`src/classifier.py`) to accept a list of items (`List[SourceItem]`) and return a list of results (`List[ClassificationResult]`).
      * In the MVP phase, the list size will be 1 (running single API calls).
      * In the future, we can change the internal implementation of this function to construct a batch prompt and execute 1 call for N items. Because the database table schemas (`classification_result`) are normalized per item, the database schema remains completely unchanged whether items are processed individually or in batches.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's View:** Model capability and stability will evolve over time. While we start with single-item calls for MVP robustness, we must retain a clean upgrade path to batch processing (e.g., grading 1000 items in one call years from now) without rewriting core schemas.
* **Status:** `[OPEN]`

---

### Topic 1.6: Console Progress Indication & Execution Feedback
* **Question:** How should our CLI modules report processing progress to the console during execution so that users (and monitoring agents) don't experience a silent or "frozen" terminal?
* **Perspectives:**
  * **Antigravity's View:**
    * **Interactive Progress Bar (e.g., `tqdm` or basic custom TTY runner):** 
      * *Pros:* Great visual experience for humans; easily shows percentage, elapsed time, and ETA.
      * *Cons:* TTY progress bars can pollute logs in automated agent environments or CI/CD pipelines (producing nested `\r` control character strings in output text files).
    * **Standardized Log Outputs (Line-by-line progress log):**
      * *Pros:* Highly readable for both humans and parsing agents; works perfectly in non-TTY terminals (e.g., background agent subprocess runs).
      * *Cons:* Less "dynamic" visual appeal.
    * **JSON Stream / Metadata updates:**
      * *Pros:* Permits calling scripts or parent agents to parse progress programmatically.
    * *Recommendation:* 
      1. Establish a **cross-module CLI standard** in [TECH_SPEC.md](file:///C:/Users/user/documents/derived-work/docs/TECH_SPEC.md) that commands must log progress.
      2. For execution loops (e.g. fetching feeds in `ingest`, running LLMs in `classify`), the code should print a line-by-line status update to `sys.stderr` every time a chunk or key percentage boundary is crossed (e.g., `[Progress] 10% (5/50 items processed)`).
      3. Support a `--json` parameter to stream progress in JSON format if requested.
      4. Avoid hard dependency on external visualization tools like `tqdm` unless it is explicitly wrapped to degrade gracefully in non-interactive terminals.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** *(Pending)*
* **Status:** `[OPEN]`

---

### Topic 1.7: Low-Context Feed Items and 'unknown' Classification State
* **Question:** How should we handle low-quality RSS feeds that lack sufficient context for classification? Should we introduce an `'unknown'` (or `'insufficient_context'`) classification category?
* **Perspectives:**
  * **Antigravity's View:**
    * **Highly recommended:** Yes, this makes the system far more robust. Relying purely on brief RSS titles/summaries often leads to inaccurate classifications (e.g., missing critical defense news because the feed summary was blank).
    * **State Flow:**
      * Initial Run: `source_item` -> Classify (uses RSS text) -> Topic: `'unknown'` (due to low context).
      * Future Scraping Phase: Scraper fetches the full HTML -> extracts text -> re-runs Classify (uses full text) -> Topic: `'core'`, `'adjacent'`, or `'irrelevant'`.
    * **MVP Treatment:** In the MVP phase, items classified as `'unknown'` will remain in the database and not be displayed on the website. No automated scraping is performed yet, but the data schema is fully prepared for it.
    * **DDL Changes:** Update the `topic_class` constraint in `classification_result` to:
      `CHECK (topic_class IN ('core', 'adjacent', 'irrelevant', 'unknown'))`
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** Adopt `'unknown'` as a formal `topic_class` value for low-context RSS items. In the MVP, low-context items are identified before LLM invocation using the configured `min_context_characters` threshold. These items do not require a separate `classification_status` field.
* **Status:** `[RESOLVED]`

---

### Topic 1.8: Incremental Curation for Enriched Items (Transitions from 'unknown' to 'core')
* **Question:** When an `'unknown'` item from 2 months ago is enriched and re-classified as `'core'`, how do we process it without triggering a full re-evaluation of other historical items or edited drafts?
* **Perspectives:**
  * **Antigravity's View:**
    * **State Differences:** Yes, absolutely. Relational database states make these differences explicit:
      * *Aggregated Item (Flagged core but not edited):* Linked to a `classification_result` (topic: `'core'`) and a `review_decision` (decision: `'approved'`), but does **not** have an `edit_draft` or a `published_piece` linked to it.
      * *Edited Draft:* Linked to an `edit_draft` record (whose `draft_status` might be `'draft'` or `'approved'`).
      * *Published Piece:* Linked to a `published_piece` record in the database.
    * **Simple Incremental Queries:**
      * To find items needing review: Query `classification_result` where `topic_class IN ('core', 'adjacent')` AND `source_item_id NOT IN (SELECT target_id FROM review_decision)`.
      * To find approved items needing publish export: Query `review_decision` where `decision = 'approved'` AND `target_id NOT IN (SELECT target_id FROM published_piece)`.
    * **Independent Item Lifecycle:** Each `source_item` has its own unique lifecycle state. When an item transitions from `'unknown'` to `'core'`, it enters the `review` queue as an individual pending item. It does not affect other items.
    * **Incremental Publishing:** Once approved, it is exported as a new Markdown file in the `publish` layer. The static site generator (Astro) reads the folder of published files and regenerates the site. Modern static site builds are extremely fast (seconds for thousands of pages), so rebuilding the whole site to include the new page is trivial.
    * **Chronological Ordering:** The item retains its original feed publication date (`published_at`) in metadata, ensuring it automatically sorts into the correct position in archives and lists, regardless of when it was cleared.
    * *Recommendation:* Rely on independent item states and the fast rebuild times of static site generation (Astro). Do not construct complex retroactive dependency graphs between articles.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** *(Pending)*
* **Status:** `[OPEN]`

---

### Topic 1.9: Multi-Model Configuration & Provider Agnosticism
* **Question:** How should `model_settings.yaml` support switching between multiple LLM providers (e.g., Gemini, OpenAI, Anthropic, DeepSeek, Qwen, MiniMax, Kimi, GLM) to handle failover, performance tuning, and cost optimization?
* **Perspectives:**
  * **Antigravity's View:**
    * **Provider Agnostic Client Wrapper:** We should structure our API connector (`src/classifier.py`) to support multiple endpoints (e.g., standard OpenAI compatibility layer, which almost all competing providers like DeepSeek, Qwen, MiniMax, and GLM support).
    * **Config Structure:** Expand `model_settings.yaml` to define providers and routing options:
      ```yaml
      active_provider: "deepseek"  # Switch easily
      providers:
        gemini:
          api_type: "google"
          model_name: "gemini-1.5-flash"
          api_key_env: "GEMINI_API_KEY"
        deepseek:
          api_type: "openai_compatible"
          api_base: "https://api.deepseek.com/v1"
          model_name: "deepseek-chat"
          api_key_env: "DEEPSEEK_API_KEY"
        qwen:
          api_type: "openai_compatible"
          api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"
          model_name: "qwen-turbo"
          api_key_env: "DASHSCOPE_API_KEY"
      ```
    * **Structured Output Fallback:** Keep in mind that some providers do not support strict JSON schema enforcement via native APIs. In those cases, the wrapper must fallback to regex-based JSON extraction from raw markdown blocks, while Google and OpenAI support strict structured outputs.
    * *Recommendation:* Implement a flexible, OpenAI-compatible wrapper in `classifier.py` and structured configuration schema in `model_settings.yaml` to ensure easy switching.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** *(Pending)*
* **Status:** `[OPEN]`

---

### Topic 1.10: Diagnostic HTML Report Exporters for Pipeline Modules
* **Question:** Should we standardize on providing diagnostic HTML report exporters (like `export-report`) for each pipeline module to help human curators inspect intermediate database states?
* **Perspectives:**
  * **Antigravity's View:**
    * **Highly supportive:** Yes, this is an excellent diagnostic capability. Visualizing intermediate processing outcomes (e.g., seeing how many items were labeled `core` vs. `adjacent` or why certain things were marked `unknown` due to low context) in a searchable, sortable HTML table is far superior to raw SQL querying or database browsers.
    * **Boundary Compliance:** Since these commands are strictly **read-only** (they query SQLite and write a standalone, static HTML file to the `data/` directory), they do not modify database state or violate module separation guidelines.
    * **Proposed Classify Report (`classify_report.html`):** We should add a `classify export-report` command that generates a diagnostic report with:
      * Overall metrics (Total classified, breakdown by category, average confidence score).
      * An interactive, searchable table with search and category filtering.
      * Highlighting for potential edge cases (e.g., `core` items with confidence < 0.70, or items flagged as `edit_candidate`).
    * **Future Modular Extraction:** For MVP, keeping the reporting code local to each module's CLI is simple and lightweight. Once the project matures, we can easily centralize report templates and queries under a shared diagnostic or helper module.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** *(Pending)*
* **Status:** `[OPEN]`

---

### Topic 1.11: Parameter Configuration (YAML vs. CLI Overrides)
* **Question:** Should operational parameters (such as `request_timeout_seconds`, `batch_size`, `max_concurrent_requests`, and `rate_limit_per_minute`) be extracted into configuration files, and should we expose CLI override flags?
* **Perspectives:**
  * **Antigravity's View:**
    * **Highly recommended:** Yes, none of these execution-tuning values should be hardcoded in Python files. Throttling and concurrency parameters vary based on API limits, network environments, and capacity.
    * **YAML Configuration Strategy:** Define these parameters in `modules/classify/config/model_settings.yaml` under `batch_policy`.
    * **No CLI Override Flags:** Keep the CLI simple and avoid polluting commands with optional arguments. Modifying the YAML configuration is the standardized way to tune these parameters.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** Reconfigured parameters must live strictly inside `model_settings.yaml`. CLI arguments will not expose override options for batch size, concurrency, rate limits, or timeouts. The user interface remains minimal.
* **Status:** `[RESOLVED]`

---

### Topic 1.12: Explicit Classification Status Field
* **Question:** Does the `classify` module need an explicit `classification_status` column in the `classification_result` table (similar to `ingest_status` in `source_item`), or is the presence of the row in the table sufficient?
* **Perspectives:**
  * **Antigravity's View:**
    * **Extensibility vs. Redundancy Analysis:** The previous design of having explicit status fields (even when timestamps exist) is a **thoughtful extensibility pattern** rather than redundant coding.
    * **Timestamps vs. State Machines:** 
      * *Timestamps* represent execution facts (e.g., "when did this action succeed?").
      * *Status fields* represent the active state of an entity within the business domain (e.g., `'ingested'`, `'classified'`, `'failed'`, `'needs_enrichment'`).
    * **Key Reasons to Prefer Explicit Status Fields:**
      1. **Handling Failures:** If classification fails (e.g., API is rate-limited or blocks the query), there is no `classified_at` timestamp. Without a status field, the database cannot distinguish between a "newly ingested item that has never been attempted" and an "attempted item that repeatedly failed". A status of `'failed'` makes this distinction explicit.
      2. **Handling Intermediate/Complex States:** If an item is classified as `'unknown'`, it might have a `classified_at` timestamp (the time it was evaluated). However, its operational status is `'pending_enrichment'`. If we only look at `classified_at IS NOT NULL`, the system would mistake it for a finalized classification.
      3. **Simpler & Safer Queries:** Querying `WHERE classification_status = 'failed'` is much cleaner and less error-prone for downstream consumers than checking combinations of NULL/NOT NULL timestamps and joined result tables.
    * *Recommendation:* Keep the explicit `classification_status` column in the DDL to support robust error-handling, retry queues, and intermediate states like enrichment.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** Do not add `classification_status` to the MVP schema. The presence of a `classification_result` row is sufficient to indicate a completed initial classification pass, and `topic_class = 'unknown'` covers low-context items. If future enrichment workflow needs richer operational state, prefer introducing a dedicated workflow table instead of overloading `classification_result`.
* **Status:** `[RESOLVED]`

---

### Topic 1.13: Configurable Prompt Template Selection
* **Question:** How should the module dynamically load prompt templates? Should the active prompt template key be configurable in `model_settings.yaml` instead of being hardcoded in Python source code?
* **Perspectives:**
  * **Antigravity's View:**
    * **Highly recommended:** Yes, the code should remain completely decoupled from prompt wording. Hardcoding the name or version of the prompt template makes it difficult to run parallel experiments or switch between single-item and batch-item modes.
    * **YAML Configuration Strategy:**
      * Organise `config/prompt_templates.yaml` to store multiple named templates (e.g. `single_item_v1.0`, `batch_items_v1.0`).
      * Define `active_prompt_template: "single_item_v1.0"` in `config/model_settings.yaml`.
      * The python classifier loaders read the active key and extract the corresponding prompt strings dynamically.
    * *Recommendation:* Implement configurable prompt template selection in configuration files to support frictionless template versioning and batch upgrade paths.
  * **Peer AI's View:** *(To be filled by peer AI)*
  * **User's Decision:** *(Pending)*
* **Status:** `[OPEN]`

---

## 2. Resolved Decisions & Consensus Log

*(This section will record topics once they have been resolved with consensus from the User and both AIs.)*

| Topic ID | Decision Summary | Date Resolved |
| :--- | :--- | :--- |
| **Topic 1.1** | Enforce a 1-to-1 relationship with a UNIQUE constraint on `source_item_id` in `classification_result`. Overwrite existing records if re-run, no historical records preserved to minimize API cost. | 2026-06-03 |
| **Topic 1.2** | Use `gpt-5.4-mini` for the initial MVP, with potential future transitions to MiniMax M3 or DeepSeek V4 Flash due to cost. If classification fails, items are left unclassified for triage rather than auto-assigning defaults. | 2026-06-03 |
| **Topic 1.3** | Trigger page-level enrichment for low-context items by marking them as `'unknown'`. The threshold for low-context classification is defined configurably as `min_context_characters` in `model_settings.yaml` instead of being hardcoded. | 2026-06-03 |
| **Topic 1.7** | Adopt `'unknown'` as a formal `topic_class` for low-context feed items. In the MVP, these items are detected before LLM invocation and do not require a separate `classification_status` field. | 2026-06-03 |
| **Topic 1.4** | Deferred. SLA policies and automated triage logic belong strictly to Stage 3 (`review`) and will not be implemented in the `classify` module. | 2026-06-03 |
| **Topic 1.11** | Operational parameters live signatures strictly in `model_settings.yaml`; CLI overrides are omitted to keep the user interface simple. | 2026-06-03 |
| **Topic 1.12** | Omit `classification_status` from the MVP schema. If future enrichment workflow needs explicit operational state, prefer a dedicated workflow table instead of extending `classification_result`. | 2026-06-03 |
