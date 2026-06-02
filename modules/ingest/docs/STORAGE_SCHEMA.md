# Ingest Storage Schema Specification

**Document version:** v1.0  
**Updated:** 2026-05-30  
**Status:** Implementation Locked (Concrete Specification)  
**Reference:** Supersedes directions from `archive/STORAGE_SCHEMA_DRAFT.md` and resolves all findings in `archive/STORAGE_SCHEMA_LOCK_CHECKLIST.md`.

---

## 1. Column Types & Timestamp Policy

To ensure robust local operation with SQLite and seamless future migration to PostgreSQL:
- **Timestamps:** Standardized strictly to **UTC ISO-8601 second-precision text: `YYYY-MM-DDTHH:MM:SSZ`** (exactly 20 characters). SQLite lacks a native datetime type; using a single, fixed-precision format ensures lexicographical sorting and range queries function correctly via `TEXT` comparisons. Timestamps must be normalized/formatted to this exact format before database insertion.
- **Statuses/Enums:** Stored as **`TEXT`** to allow readability, flexibility, and painless value expansion.
- **Identifiers:** Internal surrogate keys use **`INTEGER AUTOINCREMENT`**, except for natural integer configurations (like `source_id`).

---

## 2. Table Schemas & Nullability Matrix

### 2.1 Table: `source_state`
Stores the current mutable health and state of each source. One row per source.
Since source definitions are config-owned in MVP (loaded from `sources.yaml`), `source_id` is NOT a database Foreign Key, but application-level referential integrity is validated at run start.

| Field Name | SQLite Type | Nullability | Description / Allowed Values |
| :--- | :--- | :--- | :--- |
| `source_id` | `INTEGER` | `NOT NULL PRIMARY KEY` | Matching the `id` defined in `sources.yaml`. |
| `last_fetch_at` | `TEXT` | `NULL` | UTC ISO-8601 of the last fetch attempt. Null before first fetch. |
| `last_success_at` | `TEXT` | `NULL` | UTC ISO-8601 of the last successful fetch. Null before first success. |
| `last_http_status` | `INTEGER` | `NULL` | Last HTTP response code (e.g. 200, 304, 404). Null if network/timeout error. |
| `etag` | `TEXT` | `NULL` | HTTP Cache validator. |
| `last_modified` | `TEXT` | `NULL` | HTTP Cache validator. |
| `consecutive_failures`| `INTEGER` | `NOT NULL DEFAULT 0` | Counter for failure-based health transitions. |
| `last_error_class` | `TEXT` | `NULL` | Checked Enums: `'network_error'`, `'timeout_error'`, `'http_error_4xx'`, `'http_error_5xx'`, `'parse_error'`, `'validation_error'`, `'persistence_error'`, `'unexpected_error'`. |
| `last_error_at` | `TEXT` | `NULL` | UTC ISO-8601 timestamp of last failure. |
| `health_status` | `TEXT` | `NOT NULL DEFAULT 'healthy'`| Checked Enums: `'healthy'`, `'degraded'`, `'quarantined'`. |
| `quarantine_until` | `TEXT` | `NULL` | UTC ISO-8601. Non-null if health is `'quarantined'`. |
| `updated_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 metadata update timestamp. |

---

### 2.2 Table: `fetch_run`
Records execution batches of the Ingest module.

| Field Name | SQLite Type | Nullability | Description / Allowed Values |
| :--- | :--- | :--- | :--- |
| `fetch_run_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Auto-incremented batch run identifier. |
| `started_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 run start. |
| `ended_at` | `TEXT` | `NULL` | UTC ISO-8601 run end. Null while running. |
| `run_scope` | `TEXT` | `NOT NULL` | Scope identifier (e.g., `'all'` or serialized shard list like `'[1,2]'`). |
| `trigger_type` | `TEXT` | `NOT NULL` | Checked Enums: `'scheduled'`, `'manual'`, `'recovery'`. |
| `run_status` | `TEXT` | `NOT NULL` | Checked Enums: `'running'`, `'success'`, `'partial_failure'`, `'failed'`. |
| `due_source_count` | `INTEGER` | `NOT NULL` | Total sources targeted for this run at start. |
| `attempted_source_count`| `INTEGER` | `NOT NULL DEFAULT 0` | Sources actually attempted. |
| `succeeded_source_count`| `INTEGER` | `NOT NULL DEFAULT 0` | Sources successfully fetched (including 304). |
| `failed_source_count` | `INTEGER` | `NOT NULL DEFAULT 0` | Sources that returned an error. |
| `error_summary` | `TEXT` | `NULL` | Structured JSON or text summarizing run-level errors. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

---

### 2.3 Table: `fetch_attempt`
Records the outcome of a single source within a specific execution run.

| Field Name | SQLite Type | Nullability | Description / Allowed Values |
| :--- | :--- | :--- | :--- |
| `fetch_attempt_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Primary Key. |
| `fetch_run_id` | `INTEGER` | `NOT NULL` | Foreign Key referencing `fetch_run(fetch_run_id) ON DELETE CASCADE`. |
| `source_id` | `INTEGER` | `NOT NULL` | Source identifier from config. |
| `attempt_started_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 source fetch start. |
| `attempt_ended_at` | `TEXT` | `NULL` | UTC ISO-8601 source fetch end. Null while active. |
| `retry_count` | `INTEGER` | `NOT NULL DEFAULT 0` | Bounded retries executed for this attempt. |
| `http_status` | `INTEGER` | `NULL` | HTTP response code (e.g., 200, 304). Null if timeout/network error. |
| `error_class` | `TEXT` | `NULL` | Checked Enums (same as `source_state`). Null if outcome is `'success'`. |
| `error_detail` | `TEXT` | `NULL` | Exception traceback, payload capture, or system logs. |
| `outcome` | `TEXT` | `NOT NULL` | Checked Enums: `'success'`, `'failed'`. |
| `new_item_count` | `INTEGER` | `NOT NULL DEFAULT 0` | Number of newly ingested feed items. |
| `dedup_matched_count` | `INTEGER` | `NOT NULL DEFAULT 0` | Number of skipped items due to deduplication. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

**Unique Constraint:** An explicit `UNIQUE(fetch_run_id, source_id)` constraint is enforced to guarantee one final attempt record per source per run.

---

### 2.4 Table: `source_item`
Stores normalized canonical entries parsed from external feeds.
- Effectively **immutable** once inserted to preserve historical audit trails.
- Contains no downstream classification or review statuses, strictly respecting boundary guidelines.

| Field Name | SQLite Type | Nullability | Description / Allowed Values |
| :--- | :--- | :--- | :--- |
| `source_item_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Primary Key. |
| `source_id` | `INTEGER` | `NOT NULL` | Config source identifier. |
| `source_item_guid` | `TEXT` | `NULL` | Original feed entry GUID. Nullable if feed lacks GUIDs. |
| `canonical_url` | `TEXT` | `NULL` | Normalized URL. Null if unavailable (do NOT fallback to feed XML URL). |
| `title` | `TEXT` | `NOT NULL` | Trimmed and collapsed whitespace title. |
| `summary` | `TEXT` | `NULL` | Entry summary/description. |
| `published_at` | `TEXT` | `NULL` | UTC ISO-8601 published date. Null if missing/unparseable. |
| `fetched_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 ingestion time. |
| `ingest_dedup_key` | `TEXT` | `NOT NULL` | Generated rule-prefixed deduplication key. |
| `dedup_rule` | `TEXT` | `NOT NULL` | Checked Enums: `'guid'`, `'url'`, `'tp'`, `'fh'`. |
| `ingest_status` | `TEXT` | `NOT NULL DEFAULT 'ingested'` | Checked Enums: `'ingested'` (MVP). |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 timestamp. |

---

### 2.5 Table: `ingest_dedup_marker`
Keeps deduplication high-performance and auditability completely clear.

| Field Name | SQLite Type | Nullability | Description / Allowed Values |
| :--- | :--- | :--- | :--- |
| `dedup_marker_id` | `INTEGER` | `NOT NULL PRIMARY KEY AUTOINCREMENT` | Primary Key. |
| `dedup_key` | `TEXT` | `NOT NULL UNIQUE` | Rule-prefixed lookup key (e.g. `guid:<value>`, `url:<value>`, `tp:<value>`, `fh:<value>`). |
| `dedup_rule` | `TEXT` | `NOT NULL` | Checked Enums: `'guid'`, `'url'`, `'tp'`, `'fh'`. |
| `source_item_id` | `INTEGER` | `NOT NULL` | Foreign Key referencing `source_item(source_item_id) ON DELETE CASCADE`. |
| `created_at` | `TEXT` | `NOT NULL` | UTC ISO-8601 registration timestamp. |

---

## 3. Concrete SQLite DDL

> [!WARNING]
> **Important SQLite Foreign Key Behavior:**
> The `PRAGMA foreign_keys = ON;` statement is included in the DDL as standard practice. However, this setting is **session-scoped** (per database connection). 
> The application code MUST explicitly execute `PRAGMA foreign_keys = ON;` upon opening every database connection in runtime code to ensure foreign key integrity constraints are actively enforced.

```sql
-- Initial Schema Migration: v001_initial_ingest_tables.sql
PRAGMA foreign_keys = ON;

-- 1. Create source_state Table
CREATE TABLE IF NOT EXISTS source_state (
    source_id INTEGER PRIMARY KEY,
    last_fetch_at TEXT,
    last_success_at TEXT,
    last_http_status INTEGER,
    etag TEXT,
    last_modified TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error_class TEXT CHECK (last_error_class IN (
        'network_error', 'timeout_error', 'http_error_4xx', 'http_error_5xx', 
        'parse_error', 'validation_error', 'persistence_error', 'unexpected_error'
    )),
    last_error_at TEXT,
    health_status TEXT NOT NULL DEFAULT 'healthy' CHECK (health_status IN ('healthy', 'degraded', 'quarantined')),
    quarantine_until TEXT,
    updated_at TEXT NOT NULL
);

-- 2. Create fetch_run Table
CREATE TABLE IF NOT EXISTS fetch_run (
    fetch_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    run_scope TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('scheduled', 'manual', 'recovery')),
    run_status TEXT NOT NULL CHECK (run_status IN ('running', 'success', 'partial_failure', 'failed')),
    due_source_count INTEGER NOT NULL,
    attempted_source_count INTEGER NOT NULL DEFAULT 0,
    succeeded_source_count INTEGER NOT NULL DEFAULT 0,
    failed_source_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    created_at TEXT NOT NULL
);

-- 3. Create fetch_attempt Table
CREATE TABLE IF NOT EXISTS fetch_attempt (
    fetch_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_run_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    attempt_started_at TEXT NOT NULL,
    attempt_ended_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    error_class TEXT CHECK (error_class IN (
        'network_error', 'timeout_error', 'http_error_4xx', 'http_error_5xx', 
        'parse_error', 'validation_error', 'persistence_error', 'unexpected_error'
    )),
    error_detail TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failed')),
    new_item_count INTEGER NOT NULL DEFAULT 0,
    dedup_matched_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (fetch_run_id) REFERENCES fetch_run(fetch_run_id) ON DELETE CASCADE,
    UNIQUE(fetch_run_id, source_id)
);

-- 4. Create source_item Table
CREATE TABLE IF NOT EXISTS source_item (
    source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    source_item_guid TEXT,
    canonical_url TEXT, -- Nullable if unavailable
    title TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    ingest_dedup_key TEXT NOT NULL,
    dedup_rule TEXT NOT NULL CHECK (dedup_rule IN ('guid', 'url', 'tp', 'fh')),
    ingest_status TEXT NOT NULL DEFAULT 'ingested' CHECK (ingest_status IN ('ingested')),
    created_at TEXT NOT NULL
);

-- 5. Create ingest_dedup_marker Table
CREATE TABLE IF NOT EXISTS ingest_dedup_marker (
    dedup_marker_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL UNIQUE,
    dedup_rule TEXT NOT NULL CHECK (dedup_rule IN ('guid', 'url', 'tp', 'fh')),
    source_item_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item(source_item_id) ON DELETE CASCADE
);

-- 6. Core Database Indexes for Performance & Constraints
CREATE INDEX IF NOT EXISTS idx_fetch_attempt_run_id ON fetch_attempt(fetch_run_id);
CREATE INDEX IF NOT EXISTS idx_source_item_source_id ON source_item(source_id);
CREATE INDEX IF NOT EXISTS idx_source_item_published_at ON source_item(published_at);
CREATE INDEX IF NOT EXISTS idx_source_item_dedup_key ON source_item(ingest_dedup_key);
CREATE INDEX IF NOT EXISTS idx_dedup_marker_source_item_id ON ingest_dedup_marker(source_item_id);
```

---

## 4. Write Paths & Transaction Boundaries

Each source fetch attempt must run in its own transaction:
1. **BEGIN IMMEDIATE TRANSACTION;**
2. For each feed entry, calculate the prefixed candidate `dedup_key`.
3. Query `ingest_dedup_marker` to see if the key exists.
4. If it exists, skip insertion (increment `dedup_matched_count`).
5. If new, insert into `source_item`, fetch new `source_item_id`, insert into `ingest_dedup_marker`, increment `new_item_count`.
6. Insert `fetch_attempt` record.
7. Update or Insert (Upsert) `source_state` for the source.
8. **COMMIT;** on success, or **ROLLBACK;** on any unhandled error.
