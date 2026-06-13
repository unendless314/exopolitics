PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS classification_result (
    classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    topic_class TEXT NOT NULL CHECK (topic_class IN ('core', 'adjacent', 'irrelevant', 'unknown')),
    classification_reason TEXT,
    classification_confidence REAL CHECK (classification_confidence IS NULL OR (classification_confidence >= 0.0 AND classification_confidence <= 1.0)),
    content_density TEXT CHECK (content_density IS NULL OR content_density IN ('low', 'medium', 'high')),
    source_text_quality TEXT CHECK (source_text_quality IS NULL OR source_text_quality IN ('poor', 'usable', 'strong')),
    primary_language_code TEXT,
    governmental_involvement INTEGER CHECK (governmental_involvement IS NULL OR governmental_involvement IN (0, 1)),
    additional_signals TEXT,
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_classification_result_topic_class
    ON classification_result(topic_class);

CREATE INDEX IF NOT EXISTS idx_classification_result_source_item_id
    ON classification_result(source_item_id);
