PRAGMA foreign_keys = ON;

-- 1. curation_decision table
CREATE TABLE IF NOT EXISTS curation_decision (
    curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    curate_status TEXT NOT NULL CHECK (curate_status IN ('approved', 'rejected', 'failed')),
    downstream_action TEXT CHECK (downstream_action IS NULL OR downstream_action IN ('publish_link', 'publish_summary', 'edit_rewrite', 'reject_discard')),
    decision_reason TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    curated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE,
    CHECK (
        (curate_status = 'failed' AND downstream_action IS NULL) OR
        (curate_status = 'approved' AND downstream_action IN ('publish_link', 'publish_summary')) OR
        (curate_status = 'rejected' AND downstream_action IN ('edit_rewrite', 'reject_discard'))
    )
);

CREATE INDEX IF NOT EXISTS idx_curation_decision_source_item_id 
    ON curation_decision(source_item_id);

CREATE INDEX IF NOT EXISTS idx_curation_decision_status_action 
    ON curation_decision(curate_status, downstream_action);


-- 2. editor_brief table
CREATE TABLE IF NOT EXISTS editor_brief (
    editor_brief_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    brief_goal TEXT NOT NULL,
    target_format TEXT NOT NULL,
    key_claim TEXT,
    key_evidence TEXT,
    required_context TEXT,
    risk_flags TEXT, -- JSON Array formatted as string
    tone_guidance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_editor_brief_source_item_id 
    ON editor_brief(source_item_id);


-- 3. curation_output table
CREATE TABLE IF NOT EXISTS curation_output (
    curation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_item_id INTEGER NOT NULL UNIQUE,
    display_title TEXT NOT NULL,
    summary_short TEXT NOT NULL,
    bullet_1 TEXT,
    bullet_2 TEXT,
    bullet_3 TEXT,
    source_attribution_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_curation_output_source_item_id 
    ON curation_output(source_item_id);
