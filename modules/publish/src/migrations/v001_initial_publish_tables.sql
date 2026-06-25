PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS publish_record (
    publish_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    first_published_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_publish_record_source_item_id
    ON publish_record(source_item_id);

CREATE INDEX IF NOT EXISTS idx_publish_record_slug
    ON publish_record(slug);

CREATE TABLE IF NOT EXISTS publish_language_status (
    publish_language_status_id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_record_id INTEGER NOT NULL,
    language_code TEXT NOT NULL,
    publish_status TEXT NOT NULL CHECK (publish_status IN ('published', 'withdrawn')),
    published_at TEXT,
    withdrawn_at TEXT,
    source_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (publish_record_id) REFERENCES publish_record (publish_record_id) ON DELETE CASCADE,
    UNIQUE (publish_record_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_publish_language_status_record_lang
    ON publish_language_status(publish_record_id, language_code);

CREATE INDEX IF NOT EXISTS idx_publish_language_status_state
    ON publish_language_status(language_code, publish_status);
