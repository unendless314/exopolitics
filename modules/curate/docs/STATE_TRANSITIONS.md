# Curation State Transitions

**Document version:** v1.1  
**Updated:** 2026-06-16  
**Status:** Planning & Active rewrite draft

---

## 1. Curation Workflow States

Every source item selected for curation moves through a set of distinct workflow states. These states are resolved from the database columns in `curation_decision` and `source_item`:

* **`pending`**: The item is eligible for automated curation. This is true if the item has `ingest_status = 'ingested'`, classification result `topic_class` in `('core', 'adjacent')`, and either:
  * No matching row exists in the `curation_decision` table.
  * A row exists with `curate_status = 'failed'` and `retry_count < 3`.
* **`approved`**: The item has been successfully curated and approved for publishing. A row exists in `curation_decision` with `curate_status = 'approved'`, and matching rows exist in `editor_brief` and `curation_output`. Crucially, approved items must satisfy one of these two routing-specific sub-states:
  * **`publish_link` sub-state**: `downstream_action = 'publish_link'`. In this sub-state, `bullet_1`, `bullet_2`, and `bullet_3` in `curation_output` **must be completely NULL**.
  * **`publish_summary` sub-state**: `downstream_action = 'publish_summary'`. In this sub-state, `bullet_1`, `bullet_2`, and `bullet_3` in `curation_output` **must all be NOT NULL**.
* **`rejected`**: The item has been evaluated and rejected based on editorial rules. A row exists in `curation_decision` with `curate_status = 'rejected'`.
  * If `downstream_action = 'edit_rewrite'`, a matching row exists in `editor_brief`, and `curation_output` is absent.
  * If `downstream_action = 'reject_discard'`, both `editor_brief` and `curation_output` are absent.
* **`failed`**: A transient error occurred during processing (e.g. rate limit, API timeout, JSON parsing failure). A row exists with `curate_status = 'failed'` and `retry_count < 3`.
* **`locked` (Failed Permanently)**: The item has failed 3 consecutive curation attempts. A row exists in `curation_decision` with `curate_status = 'failed'` and `retry_count >= 3`. It is excluded from automatic retry queues and requires manual override or deletion to be reprocessed.
* **`withdrawn`**: A previously approved item that was manually withdrawn/taken down by an operator. A row exists in `curation_decision` with `curate_status = 'withdrawn'`. Corresponding records in `editor_brief`, `curation_output`, `approved_content_record`, and `translation_output` remain present in the database to serve as cache anchors, but are no longer active for public export.

---

## 2. State Transition Matrix

The table below defines how an item transitions from its **Old State** to a **New State** based on the outcome of a runner execution.

| Old State | Runner Trigger | New State | Curation Decision Updates | Side-Effects & Data Cleanup |
| :--- | :--- | :--- | :--- | :--- |
| **None / Pending** | LLM success (`publish_link` / `publish_summary`) | **approved** | Insert `curation_decision` (status='approved', action, retry_count=0) | Insert/Update `editor_brief` and `curation_output`. |
| **None / Pending** | LLM success (`edit_rewrite`) | **rejected** | Insert `curation_decision` (status='rejected', action='edit_rewrite', retry_count=0) | Insert/Update `editor_brief`. Delete any existing `curation_output`. |
| **None / Pending** | LLM success (`reject_discard`) | **rejected** | Insert `curation_decision` (status='rejected', action='reject_discard', retry_count=0) | Delete any existing `editor_brief` and `curation_output`. |
| **None / Pending** | Transient Runner Failure | **failed** | Insert `curation_decision` (status='failed', action=NULL, retry_count=1) | None. |
| **failed** (retry < 2) | Transient Runner Failure | **failed** | Update `curation_decision` (status='failed', action=NULL, retry_count = retry_count + 1) | None. |
| **failed** (retry = 2) | Transient Runner Failure | **locked** | Update `curation_decision` (status='failed', action=NULL, retry_count = 3) | None. Lock out item from queue. |
| **failed** | LLM success (`publish_link` / `publish_summary`) | **approved** | Update `curation_decision` (status='approved', action, retry_count=0) | Insert `editor_brief` and `curation_output`. |
| **failed** | LLM success (`edit_rewrite`) | **rejected** | Update `curation_decision` (status='rejected', action='edit_rewrite', retry_count=0) | Insert `editor_brief`. |
| **failed** | LLM success (`reject_discard`) | **rejected** | Update `curation_decision` (status='rejected', action='reject_discard', retry_count=0) | None. |
| **approved / rejected** | Forced Re-run Success (`publish_*`) | **approved** | Update `curation_decision` (status='approved', action, retry_count=0) | Update/Insert `editor_brief` and `curation_output`. |
| **approved / rejected** | Forced Re-run Success (`edit_rewrite`) | **rejected** | Update `curation_decision` (status='rejected', action='edit_rewrite', retry_count=0) | Update/Insert `editor_brief`. Delete existing `curation_output` row if present. |
| **approved / rejected** | Forced Re-run Success (`reject_discard`) | **rejected** | Update `curation_decision` (status='rejected', action='reject_discard', retry_count=0) | Delete existing `editor_brief` and `curation_output` rows if present. |
| **approved / rejected** | Forced Re-run Failure | **Unchanged** | None. Transaction rolled back. | No database changes. Keep previous successful curation results. |
| **approved** | Operator triggers `withdraw` | **withdrawn** | Update `curation_decision` (status='withdrawn', reason, actor='operator', updated_at=now) | Keep existing `editor_brief` and `curation_output` rows (protect translation cache). |
| **withdrawn** | Operator triggers `reapprove` | **approved** | Update `curation_decision` (status='approved', reason, actor='operator', updated_at=now) | Keep existing `editor_brief` and `curation_output` rows. |
| **withdrawn** | Forced Re-run Success (`publish_*`) | **approved** | Update `curation_decision` (status='approved', action, actor='system', retry_count=0, updated_at=now) | Update/Insert `editor_brief` and `curation_output`. |
| **withdrawn** | Forced Re-run Success (`edit_rewrite`) | **rejected** | Update `curation_decision` (status='rejected', action='edit_rewrite', actor='system', retry_count=0, updated_at=now) | Update/Insert `editor_brief`. Delete existing `curation_output` row if present. |
| **withdrawn** | Forced Re-run Success (`reject_discard`) | **rejected** | Update `curation_decision` (status='rejected', action='reject_discard', actor='system', retry_count=0, updated_at=now) | Delete existing `editor_brief` and `curation_output` rows if present. |
| **withdrawn** | Forced Re-run Failure | **Unchanged** | None. Transaction rolled back. | No database changes. Keep previous curation results. |

---

## 3. Re-Curation & Overwriting Policies

When a curation decision is re-run and updated, the repository must enforce strict data consistency rules. SQLite cascade deletes alone do not handle transitions between different active actions (e.g. from approved to rejected). The repository must perform explicit deletes:

1. **Changing from Approved (`publish_link`/`publish_summary`) to Rejected (`reject_discard`)**:
   * The `editor_brief` row for the `source_item_id` **must be deleted**.
   * The `curation_output` row for the `source_item_id` **must be deleted**.
2. **Changing from Approved (`publish_link`/`publish_summary`) to Rewrite (`edit_rewrite`)**:
   * The `editor_brief` row is updated with the new LLM output.
   * The `curation_output` row for the `source_item_id` **must be deleted**.
3. **Changing from Rewrite (`edit_rewrite`) to Approved (`publish_link`/`publish_summary`)**:
   * The `editor_brief` row is updated with the new LLM output.
   * A new `curation_output` row is created.
4. **Forced Re-run Failure Handling**:
   * **Scope & Rule:** Only normal executions targeting items in the `pending` or `failed` queues will write a `failed` status and increment/set `retry_count` upon failure. For any items that are already in `approved` or `rejected` states, if a manual/operator-forced re-run fails (e.g., due to LLM API exceptions or parsing failures), the runner must rollback the transaction entirely and leave the existing successful/rejected curation decision, editor brief, and curation output rows completely unchanged in the database (i.e., do not write `failed` status, do not increment retry count, and do not delete existing summaries).
