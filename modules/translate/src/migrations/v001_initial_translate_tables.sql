PRAGMA foreign_keys = ON;

-- 1. approved_content_record (Shared Handoff Capability - Co-located)
CREATE TABLE IF NOT EXISTS approved_content_record (
    parent_content_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    display_title TEXT NOT NULL,
    content_body TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    content_language_code TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    author_metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_approved_content_record_source_item_id 
    ON approved_content_record(source_item_id);

CREATE INDEX IF NOT EXISTS idx_approved_content_record_fingerprint 
    ON approved_content_record(content_fingerprint);

-- 2. translation_output (Translate Module)
CREATE TABLE IF NOT EXISTS translation_output (
    translation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_content_id INTEGER NOT NULL,
    source_item_id INTEGER NOT NULL,
    language_code TEXT NOT NULL,
    display_title TEXT,
    content TEXT,
    source_fingerprint TEXT NOT NULL,
    translation_status TEXT NOT NULL CHECK (translation_status IN ('pending', 'completed', 'failed', 'stale')),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    translated_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (parent_content_id) REFERENCES approved_content_record (parent_content_id) ON DELETE CASCADE,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id),
    UNIQUE (parent_content_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_translation_output_parent_lang 
    ON translation_output(parent_content_id, language_code);

CREATE INDEX IF NOT EXISTS idx_translation_output_status 
    ON translation_output(translation_status);
