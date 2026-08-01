PRAGMA foreign_keys = ON;

-- Publish-owned logical write timestamps for monthly archive files.
-- archives/index.json (the manifest) reads updated_at from this table so the
-- value reflects the most recent successful archive write/rewrite instead of
-- an aggregate over item-level publish timestamps. Rows are deleted when the
-- corresponding archive file becomes empty and is removed.
CREATE TABLE IF NOT EXISTS publish_archive_metadata (
    language_code TEXT NOT NULL,
    archive_month TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (language_code, archive_month)
);
