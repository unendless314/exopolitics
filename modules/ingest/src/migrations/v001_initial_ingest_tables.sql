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
    error_summary TEXT
);

-- 3. Create fetch_attempt Table
CREATE TABLE IF NOT EXISTS fetch_attempt (
    fetch_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_run_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
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
    low_context_count INTEGER NOT NULL DEFAULT 0,
    sanitization_failure_count INTEGER NOT NULL DEFAULT 0,
    normalization_failure_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (fetch_run_id) REFERENCES fetch_run(fetch_run_id) ON DELETE CASCADE,
    UNIQUE(fetch_run_id, source_id)
);

-- 4. Create source_item Table
CREATE TABLE IF NOT EXISTS source_item (
    source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    source_item_guid TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    ingest_dedup_key TEXT NOT NULL,
    dedup_rule TEXT NOT NULL CHECK (dedup_rule IN ('guid', 'url', 'tp', 'fh')),
    ingest_status TEXT NOT NULL DEFAULT 'ingested' CHECK (ingest_status = 'ingested')
);

-- 5. Create source_item_text Table
CREATE TABLE IF NOT EXISTS source_item_text (
    source_item_text_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    sanitized_text TEXT NOT NULL,
    sanitization_method TEXT NOT NULL,
    html_detected INTEGER NOT NULL CHECK (html_detected IN (0, 1)),
    was_truncated INTEGER NOT NULL CHECK (was_truncated IN (0, 1)),
    is_low_context INTEGER NOT NULL CHECK (is_low_context IN (0, 1)),
    low_context_reason TEXT CHECK (low_context_reason IS NULL OR low_context_reason IN (
        'missing_body', 'post_cleanup_empty', 'too_short', 'title_only', 
        'title_heavy', 'template_heavy', 'mostly_links', 'truncated_to_low_context'
    )),
    raw_text_length INTEGER,
    sanitized_text_length INTEGER NOT NULL,
    reduction_ratio REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item(source_item_id) ON DELETE RESTRICT
);

-- 6. Create source_item_raw Table
CREATE TABLE IF NOT EXISTS source_item_raw (
    source_item_raw_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL,
    raw_payload TEXT NOT NULL,
    retention_class TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item(source_item_id) ON DELETE RESTRICT
);

-- 7. Create ingest_dedup_marker Table
CREATE TABLE IF NOT EXISTS ingest_dedup_marker (
    dedup_marker_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL UNIQUE,
    dedup_rule TEXT NOT NULL CHECK (dedup_rule IN ('guid', 'url', 'tp', 'fh')),
    source_item_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item(source_item_id) ON DELETE RESTRICT
);

-- 8. Create Database Indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_item_dedup_key ON source_item(ingest_dedup_key);
CREATE INDEX IF NOT EXISTS idx_source_item_source_id ON source_item(source_id);
CREATE INDEX IF NOT EXISTS idx_source_item_published_at ON source_item(published_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_item_text_source_item_id ON source_item_text(source_item_id);
CREATE INDEX IF NOT EXISTS idx_source_item_raw_source_item_id ON source_item_raw(source_item_id);
CREATE INDEX IF NOT EXISTS idx_source_item_raw_expires_at ON source_item_raw(expires_at);
CREATE INDEX IF NOT EXISTS idx_source_item_raw_retention_class ON source_item_raw(retention_class);
CREATE INDEX IF NOT EXISTS idx_fetch_attempt_run_id ON fetch_attempt(fetch_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fetch_attempt_run_source ON fetch_attempt(fetch_run_id, source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_marker_key ON ingest_dedup_marker(dedup_key);
CREATE INDEX IF NOT EXISTS idx_dedup_marker_source_item_id ON ingest_dedup_marker(source_item_id);
