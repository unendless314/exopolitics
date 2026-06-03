-- Migration: v002_initial_classify_tables.sql
-- Description: Create classification_result table and indexes for v2.0 contract.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS classification_result (
    classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    topic_class TEXT NOT NULL CHECK (topic_class IN ('core', 'adjacent', 'irrelevant', 'unknown')),
    classification_reason TEXT,
    classification_confidence REAL CHECK (classification_confidence >= 0.0 AND classification_confidence <= 1.0),
    edit_candidate INTEGER NOT NULL DEFAULT 0 CHECK (edit_candidate IN (0, 1)),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_classification_result_topic_class
    ON classification_result(topic_class);
