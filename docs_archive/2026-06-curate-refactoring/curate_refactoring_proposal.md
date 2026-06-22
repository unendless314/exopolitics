# Refactoring Proposal: Editorial-Owned Soft Withdrawal in Curate Module

This document outlines the proposed changes to the `curate` module to support the `withdrawn` status, facilitating safe, logical soft-withdrawals of published articles while fully preserving upstream translation caches. 

This design follows the收斂方案 (converged option) described in [WITHDRAWAL_DESIGN_DISCUSSION.md](file:///C:/Users/user/Documents/derived-work/modules/publish/docs/WITHDRAWAL_DESIGN_DISCUSSION.md).

---

## 1. Database Schema Refactoring (`curate` Module)

Currently, the `curate_status` column in `curation_decision` is constrained to `('approved', 'rejected', 'failed')`. To support soft withdrawal, we need to allow `withdrawn`. At the same time, we recommend a minimal metadata extension so manual editorial state changes remain distinguishable from automated curation writes without introducing a full audit subsystem yet.

### Proposed DDL Constraint Changes

We recommend updating the table constraints in [v001_initial_curate_tables.sql](file:///C:/Users/user/Documents/derived-work/modules/curate/src/migrations/v001_initial_curate_tables.sql) directly:

```diff
  -- 1. curation_decision table
  CREATE TABLE IF NOT EXISTS curation_decision (
      curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_item_id INTEGER NOT NULL UNIQUE,
-     curate_status TEXT NOT NULL CHECK (curate_status IN ('approved', 'rejected', 'failed')),
+     curate_status TEXT NOT NULL CHECK (curate_status IN ('approved', 'rejected', 'failed', 'withdrawn')),
      downstream_action TEXT CHECK (downstream_action IS NULL OR downstream_action IN ('publish_link', 'publish_summary', 'edit_rewrite', 'reject_discard')),
      decision_reason TEXT,
      decision_actor TEXT NOT NULL CHECK (decision_actor IN ('system', 'operator')),
      retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
      model_name TEXT NOT NULL,
      prompt_version TEXT NOT NULL,
      curated_at TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE,
      CHECK (
          (curate_status = 'failed' AND downstream_action IS NULL) OR
-         (curate_status = 'approved' AND downstream_action IN ('publish_link', 'publish_summary')) OR
-         (curate_status = 'rejected' AND downstream_action IN ('edit_rewrite', 'reject_discard'))
+         (curate_status = 'approved' AND downstream_action IN ('publish_link', 'publish_summary')) OR
+         (curate_status = 'rejected' AND downstream_action IN ('edit_rewrite', 'reject_discard')) OR
+         (curate_status = 'withdrawn' AND downstream_action IN ('publish_link', 'publish_summary'))
      )
  );
```

> [!IMPORTANT]
> **SQLite Table Re-creation Note:**  
> Since SQLite does not natively support `ALTER TABLE ... ADD CONSTRAINT` or altering column check constraints, modifying the check constraints in an existing database requires a table rebuild (creating a temp table, copying data, and renaming). Because the system is pre-production and data can be rebuilt from source (as per [IMPLEMENTATION_ROADMAP.md](file:///C:/Users/user/Documents/derived-work/docs/IMPLEMENTATION_ROADMAP.md)), editing the initial DDL script [v001_initial_curate_tables.sql](file:///C:/Users/user/Documents/derived-work/modules/curate/src/migrations/v001_initial_curate_tables.sql) and recreating the database is the cleanest and most recommended approach.
>
> In practice, note that the current migration runner tracks applied files in `schema_migrations`. This means simply editing `v001_initial_curate_tables.sql` will not affect an already-migrated local database unless that database is explicitly rebuilt or deleted and recreated.

> [!NOTE]
> When `curate_status` transitions to `'withdrawn'`, we explicitly preserve the original `downstream_action` (e.g. `'publish_link'` or `'publish_summary'`). This satisfies the database validation checks, leaves the associated `curation_output` and `editor_brief` intact (as soft-withdrawal does not delete them), and preserves the historical routing context of how the article was last approved for publication. It must not be treated as the canonical indicator of current publish eligibility; that remains `curate_status = 'approved'`.

### Proposed Column Semantics Adjustments

The following semantic clarifications should accompany the DDL update:

1. `decision_reason` becomes the reason for the current persisted state, regardless of whether that state was written by the automated pipeline or by a human operator.
2. `decision_actor` records who authored the persisted state transition:
   * `system`: automated curation pipeline
   * `operator`: manual editorial action such as `withdraw` or `reapprove`
3. `curated_at` should remain the timestamp of the last automated curation write.
4. `updated_at` should become the timestamp of the last mutation to the row by any actor.

This is the intended minimal contract extension. It improves state traceability without yet introducing a separate decision history table.

---

## 2. CLI Command Additions (`cli.py`)

To allow human operators to manually withdraw and re-approve articles, we propose adding two subcommands to [cli.py](file:///C:/Users/user/Documents/derived-work/modules/curate/src/cli.py).

### Required Imports Updates in `cli.py`
Add the following imports to the top of the CLI file:
```python
import datetime
from .database import transaction
```

### A. The `withdraw` Command
Changes the state of an approved item to `withdrawn`.

```python
@cli.command("withdraw")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
@click.option(
    "--reason",
    required=True,
    help="Manual withdrawal reason written into decision_reason"
)
@click.argument("source-item-id", type=int)
def cmd_withdraw(db_path, reason, source_item_id):
    """Manually withdraw a published item by source_item_id (sets status to 'withdrawn')"""
    if not db_path.exists():
        click.echo(f"Database file does not exist: {db_path}", err=True)
        sys.exit(1)

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT curate_status FROM curation_decision WHERE source_item_id = ?", (source_item_id,))
        row = cursor.fetchone()
        if not row:
            click.echo(f"No curation decision found for item ID {source_item_id}.", err=True)
            sys.exit(1)
        
        status = row["curate_status"]
        if status != "approved":
            click.echo(f"Item ID {source_item_id} is in status '{status}', only 'approved' items can be withdrawn.", err=True)
            sys.exit(1)

        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with transaction(conn):
            cursor.execute("""
                UPDATE curation_decision 
                SET curate_status = 'withdrawn',
                    decision_reason = ?,
                    decision_actor = 'operator',
                    updated_at = ?
                WHERE source_item_id = ?
            """, (reason, now, source_item_id))
        
        click.echo(f"Successfully withdrew item ID {source_item_id} (status set to 'withdrawn').")
    except Exception as e:
        click.echo(f"Error withdrawing item: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()
```

### B. The `reapprove` Command
Reverts a `withdrawn` item back to `approved`.

```python
@cli.command("reapprove")
@click.option(
    "--db-path",
    type=click.Path(path_type=pathlib.Path),
    default=DEFAULT_DB_PATH,
    help="Custom SQLite canonical database path"
)
@click.option(
    "--reason",
    required=True,
    help="Manual re-approval reason written into decision_reason"
)
@click.argument("source-item-id", type=int)
def cmd_reapprove(db_path, reason, source_item_id):
    """Re-approve a previously withdrawn item (restores status to 'approved')"""
    if not db_path.exists():
        click.echo(f"Database file does not exist: {db_path}", err=True)
        sys.exit(1)

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT curate_status FROM curation_decision WHERE source_item_id = ?", (source_item_id,))
        row = cursor.fetchone()
        if not row:
            click.echo(f"No curation decision found for item ID {source_item_id}.", err=True)
            sys.exit(1)

        status = row["curate_status"]
        if status != "withdrawn":
            click.echo(f"Item ID {source_item_id} is in status '{status}', only 'withdrawn' items can be re-approved.", err=True)
            sys.exit(1)

        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with transaction(conn):
            cursor.execute("""
                UPDATE curation_decision 
                SET curate_status = 'approved',
                    decision_reason = ?,
                    decision_actor = 'operator',
                    updated_at = ?
                WHERE source_item_id = ?
            """, (reason, now, source_item_id))

        click.echo(f"Successfully re-approved item ID {source_item_id} (status restored to 'approved').")
    except Exception as e:
        click.echo(f"Error re-approving item: {e}", err=True)
        sys.exit(1)
    finally:
        conn.close()
```

> [!NOTE]
> The proposal intentionally does not update `curated_at` during manual `withdraw` / `reapprove`. `curated_at` remains reserved for automated curation writes, while `updated_at` records the human-driven status mutation time.

---

## 3. Runner & Curation Queue Logic Refactoring (`orchestrator.py`)

To prevent the automated curation queue from picking up or re-processing `withdrawn` items, we must:

1. Ensure they are excluded from the pending items search query in `CurationRepository.get_pending_items()`. This is already true by default, as the SQL statement specifically filters for `curation_decision_id IS NULL OR (curate_status = 'failed' AND retry_count < 3)`.
2. Ensure that manually targeting a `withdrawn` item via `python -m modules.curate.src.cli run --source-item-id <ID>` requires the `--force` flag (matching the behavior of `approved` and `rejected` states).
3. Ensure that `withdrawn` is treated as a completed/operator-owned state during failure handling, so a failed forced re-run does not overwrite the manual withdrawal with `failed`.

```diff
# modules/curate/src/orchestrator.py

     existing = repo.get_curation_decision(source_item_id)
     if existing and not force:
-        if existing["curate_status"] in ("approved", "rejected"):
-            raise ValueError(f"Source item with ID {source_item_id} has already been curated (status: {existing['curate_status']}, action: {existing['downstream_action']}). Use --force to re-curate.")
+        if existing["curate_status"] in ("approved", "rejected", "withdrawn"):
+            raise ValueError(f"Source item with ID {source_item_id} has already been curated (status: {existing['curate_status']}, action: {existing['downstream_action']}). Use --force to re-curate.")
```

In addition, `curate_item()` should consider `withdrawn` as part of the completed-state family for rollback behavior, so forced re-run failures preserve the operator-authored state exactly as they already do for `approved` and `rejected`.

Finally, automated curation writes should stamp `decision_actor = 'system'` and update `updated_at` alongside the existing row fields.

---

## 4. Documentation Changes

The following changes will be applied to the `curate` module's technical specifications:

### [DATA_CONTRACT.md](file:///C:/Users/user/Documents/derived-work/modules/curate/docs/DATA_CONTRACT.md)
Update the description of `curate_status`, `downstream_action`, `decision_reason`, `decision_actor`, and `updated_at` under Section 2.1 (`curation_decision` table metadata) and update the DDL schema code snippet in Section 3 to include `'withdrawn'` and the updated outer `CHECK` constraint.

### [STATE_TRANSITIONS.md](file:///C:/Users/user/Documents/derived-work/modules/curate/docs/STATE_TRANSITIONS.md)
* Add a section for the **`withdrawn`** state:
  * **`withdrawn`**: A previously approved item that was manually withdrawn/taken down by an operator. A row exists in `curation_decision` with `curate_status = 'withdrawn'`. Corresponding records in `editor_brief`, `curation_output`, `approved_content_record`, and `translation_output` remain present in the database to serve as cache anchors, but are no longer active for public export.
* Add transitions into the **State Transition Matrix**:
  * `approved` -> Operator triggers `withdraw` -> `withdrawn` (State update: `curate_status = 'withdrawn'`, `decision_actor = 'operator'`, `updated_at` refreshed; briefs/outputs are kept).
  * `withdrawn` -> Operator triggers `reapprove` -> `approved` (State update: `curate_status = 'approved'`, `decision_actor = 'operator'`, `updated_at` refreshed).
  * `withdrawn` -> Forced Re-run Success (`publish_*`) -> `approved` (State update: resets status, regenerates briefs/outputs).
  * `withdrawn` -> Forced Re-run Success (`edit_rewrite`) -> `rejected` (State update: updates status, updates briefs, deletes curation output).
  * `withdrawn` -> Forced Re-run Success (`reject_discard`) -> `rejected` (State update: updates status, deletes briefs and curation output).
  * `withdrawn` -> Forced Re-run Failure -> `Unchanged` (Rollbacks transaction, keeps previous state).

### Additional CLI / Status Documentation

Because `withdrawn` becomes a first-class persisted state, the `status` CLI output should also add a dedicated `withdrawn` count so operators can distinguish currently publishable approvals from manually withdrawn approvals.

---

## 5. Downstream Integration Flow

### Handoff Assembler (`translate` module)
When [assemble_approved_content_records](file:///C:/Users/user/Documents/derived-work/modules/translate/src/approved_content_record.py#L54-L84) is run:
* It queries using `WHERE d.curate_status = 'approved'`.
* As a result, items that have been updated to `curate_status = 'withdrawn'` will be naturally skipped by the query.
* Since the assembler uses a delta-oriented approach and only updates or inserts based on the returned candidate list, the existing row in `approved_content_record` for the withdrawn item is left **untouched and preserved**. This preserves the translation cache (`translation_output` rows that reference it).

### Publisher (`publish` module)
When the new `publish` module runs:
* The query retrieving publishable items will fetch records where `curate_status = 'approved'`.
* For any item currently exported as a static JSON file on disk, the publisher checks the upstream status. If the upstream status in `curation_decision` is `'withdrawn'`, the publisher:
  1. Deletes the individual static JSON file under `data/publish_export/<lang>/items/<slug>.json`.
  2. Updates its local `publish_language_status` table (`publish_status` set to `'withdrawn'`).
  3. Rebuilds the static index files (`index.json` / `feed.xml`) excluding the withdrawn item.
* If a previously withdrawn item is set back to `curate_status = 'approved'`, the next `publish` run will detect it as eligible, regenerate the static JSON files, update `publish_language_status` to `'published'`, and append it back to the list indices.
* Because the database records were never deleted, this process avoids repeating expensive translation API requests.

> [!NOTE]
> Any `publish_language_status` or analogous publish-layer status table should be treated only as a downstream projection of export activity. It must not become the source of truth for withdrawal decisions, which remain owned by `curation_decision.curate_status` in the editorial domain.
