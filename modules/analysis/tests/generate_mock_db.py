import sqlite3
import pathlib
import datetime
import random
import sys

DDL_STATEMENTS = [
    # Ingest module tables
    """
    CREATE TABLE IF NOT EXISTS source_state (
        source_id INTEGER PRIMARY KEY,
        last_fetch_at TEXT,
        last_success_at TEXT,
        last_http_status INTEGER,
        etag TEXT,
        last_modified TEXT,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        last_error_class TEXT,
        last_error_at TEXT,
        health_status TEXT NOT NULL DEFAULT 'healthy',
        quarantine_until TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_run (
        fetch_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        run_scope TEXT NOT NULL,
        trigger_type TEXT NOT NULL,
        run_status TEXT NOT NULL,
        due_source_count INTEGER NOT NULL,
        attempted_source_count INTEGER NOT NULL DEFAULT 0,
        succeeded_source_count INTEGER NOT NULL DEFAULT 0,
        failed_source_count INTEGER NOT NULL DEFAULT 0,
        error_summary TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS fetch_attempt (
        fetch_attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
        fetch_run_id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        http_status INTEGER,
        error_class TEXT,
        error_detail TEXT,
        outcome TEXT NOT NULL,
        new_item_count INTEGER NOT NULL DEFAULT 0,
        dedup_matched_count INTEGER NOT NULL DEFAULT 0,
        low_context_count INTEGER NOT NULL DEFAULT 0,
        sanitization_failure_count INTEGER NOT NULL DEFAULT 0,
        normalization_failure_count INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (fetch_run_id) REFERENCES fetch_run(fetch_run_id) ON DELETE CASCADE,
        UNIQUE(fetch_run_id, source_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS source_item (
        source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL,
        source_item_guid TEXT,
        canonical_url TEXT,
        title TEXT NOT NULL,
        published_at TEXT,
        fetched_at TEXT NOT NULL,
        ingest_dedup_key TEXT NOT NULL UNIQUE,
        dedup_rule TEXT NOT NULL,
        ingest_status TEXT NOT NULL DEFAULT 'ingested'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS source_item_text (
        source_item_text_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL UNIQUE,
        sanitized_text TEXT NOT NULL,
        sanitization_method TEXT NOT NULL,
        html_detected INTEGER NOT NULL,
        was_truncated INTEGER NOT NULL,
        text_processing_status TEXT NOT NULL,
        text_processing_reason TEXT,
        raw_text_length INTEGER,
        sanitized_text_length INTEGER NOT NULL,
        reduction_ratio REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item(source_item_id) ON DELETE RESTRICT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS source_item_raw (
        source_item_raw_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL,
        raw_payload TEXT NOT NULL,
        retention_class TEXT NOT NULL,
        expires_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item(source_item_id) ON DELETE RESTRICT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_dedup_marker (
        dedup_marker_id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT NOT NULL UNIQUE,
        dedup_rule TEXT NOT NULL,
        source_item_id INTEGER NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item(source_item_id) ON DELETE RESTRICT
    );
    """,
    # Classify module tables
    """
    CREATE TABLE IF NOT EXISTS classification_result (
        classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL UNIQUE,
        topic_class TEXT NOT NULL,
        classification_reason TEXT,
        classification_confidence REAL,
        content_density TEXT,
        source_text_quality TEXT,
        primary_language_code TEXT,
        governmental_involvement INTEGER,
        additional_signals TEXT,
        model_name TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        classified_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
    );
    """,
    # Curate module tables
    """
    CREATE TABLE IF NOT EXISTS curation_decision (
        curation_decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL UNIQUE,
        curate_status TEXT NOT NULL,
        downstream_action TEXT,
        decision_reason TEXT,
        decision_actor TEXT NOT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        model_name TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        curated_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS editor_brief (
        editor_brief_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL UNIQUE,
        brief_goal TEXT NOT NULL,
        target_format TEXT NOT NULL,
        key_claim TEXT,
        key_evidence TEXT,
        required_context TEXT,
        risk_flags TEXT,
        tone_guidance TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS curation_output (
        curation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL UNIQUE,
        display_title TEXT NOT NULL,
        summary_short TEXT NOT NULL,
        bullet_1 TEXT,
        bullet_2 TEXT,
        bullet_3 TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
    );
    """,
    # Translate module tables
    """
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
    """,
    """
    CREATE TABLE IF NOT EXISTS translation_output (
        translation_output_id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_content_id INTEGER NOT NULL,
        source_item_id INTEGER NOT NULL,
        language_code TEXT NOT NULL,
        display_title TEXT,
        content TEXT,
        source_fingerprint TEXT NOT NULL,
        translation_status TEXT NOT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        model_name TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        translated_at TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (parent_content_id) REFERENCES approved_content_record (parent_content_id) ON DELETE CASCADE,
        FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id),
        UNIQUE (parent_content_id, language_code)
    );
    """,
    # Publish module tables
    """
    CREATE TABLE IF NOT EXISTS publish_record (
        publish_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        first_published_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS publish_language_status (
        publish_language_status_id INTEGER PRIMARY KEY AUTOINCREMENT,
        publish_record_id INTEGER NOT NULL,
        language_code TEXT NOT NULL,
        publish_status TEXT NOT NULL,
        published_at TEXT,
        withdrawn_at TEXT,
        source_fingerprint TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (publish_record_id) REFERENCES publish_record (publish_record_id) ON DELETE CASCADE,
        UNIQUE (publish_record_id, language_code)
    );
    """
]

def create_and_seed_db(db_path: pathlib.Path):
    print(f"Creating database at {db_path}...")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
        
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Run DDL
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()

    # Time constants in UTC
    now = datetime.datetime.now(datetime.timezone.utc)
    t_1d = now - datetime.timedelta(days=1)
    t_2d = now - datetime.timedelta(days=2)
    t_3d = now - datetime.timedelta(days=3)
    t_5d = now - datetime.timedelta(days=5)
    t_10d = now - datetime.timedelta(days=10)
    t_15d = now - datetime.timedelta(days=15)

    def to_str(dt: datetime.datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Seed source states (from ingest/config/sources.yaml IDs: 1 to 8)
    # ids: 1 to 8
    # 7 is disabled. Let's make 6 degrade, etc.
    source_states = [
        (1, to_str(t_1d), to_str(t_1d), 200, "con1", "mod1", 0, None, None, "healthy", None, to_str(t_1d)),
        (2, to_str(t_2d), to_str(t_2d), 200, "con2", "mod2", 0, None, None, "healthy", None, to_str(t_2d)),
        (3, to_str(t_1d), to_str(t_1d), 200, "con3", "mod3", 0, None, None, "healthy", None, to_str(t_1d)),
        (4, to_str(t_3d), to_str(t_3d), 200, "con4", "mod4", 0, None, None, "healthy", None, to_str(t_3d)),
        (5, to_str(t_1d), to_str(t_1d), 200, "con5", "mod5", 0, None, None, "healthy", None, to_str(t_1d)),
        (6, to_str(t_1d), to_str(t_2d), 503, None, None, 3, "http_error_5xx", to_str(t_1d), "degraded", None, to_str(t_1d)),
        (7, to_str(t_15d), to_str(t_15d), 403, None, None, 10, "http_error_4xx", to_str(t_10d), "quarantined", to_str(now + datetime.timedelta(days=5)), to_str(t_10d)),
        (8, to_str(t_1d), to_str(t_1d), 200, "con8", "mod8", 0, None, None, "healthy", None, to_str(t_1d))
    ]
    
    conn.executemany("""
        INSERT INTO source_state (
            source_id, last_fetch_at, last_success_at, last_http_status,
            etag, last_modified, consecutive_failures, last_error_class,
            last_error_at, health_status, quarantine_until, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, source_states)

    # Seed source items and text
    # We will seed items inside the 7 days window (cohort) and some outside.
    # total items: 15
    items = [
        # within lookback
        (1, 1, "guid-1", "http://url-1", "UFO sighting over Texas", to_str(t_1d), to_str(t_1d), "key-1", "guid"),
        (2, 1, "guid-2", "http://url-2", "Official briefing on UAP", to_str(t_1d), to_str(t_1d), "key-2", "guid"),
        (3, 2, "guid-3", "http://url-3", "Fotocat updates July 2026", to_str(t_2d), to_str(t_2d), "key-3", "guid"),
        (4, 3, "guid-4", "http://url-4", "Skeptic Analysis of Roswell", to_str(t_1d), to_str(t_1d), "key-4", "guid"),
        (5, 4, "guid-5", "http://url-5", "Academic study on unexplained lights", to_str(t_3d), to_str(t_3d), "key-5", "guid"),
        (6, 5, "guid-6", "http://url-6", "Australia historical cases report", to_str(t_2d), to_str(t_2d), "key-6", "guid"),
        (7, 6, "guid-7", "http://url-7", "NewsNation interview: whistleblowers", to_str(t_1d), to_str(t_1d), "key-7", "guid"),
        (8, 8, "guid-8", "http://url-8", "Openminds live disclosure panel", to_str(t_1d), to_str(t_1d), "key-8", "guid"),
        
        # low context example
        (9, 1, "guid-9", "http://url-9", "Short news snippet", to_str(t_1d), to_str(t_1d), "key-9", "guid"),
        
        # outside lookback (10 days ago)
        (10, 1, "guid-10", "http://url-10", "Old MUFON case study", to_str(t_10d), to_str(t_10d), "key-10", "guid"),
        (11, 2, "guid-11", "http://url-11", "Old Fotocat case study", to_str(t_10d), to_str(t_10d), "key-11", "guid"),
        (12, 3, "guid-12", "http://url-12", "Old bad ufos review", to_str(t_15d), to_str(t_15d), "key-12", "guid"),
        
        # another within lookback
        (13, 2, "guid-13", "http://url-13", "Another Fotocat entry", to_str(t_3d), to_str(t_3d), "key-13", "guid"),
        (14, 5, "guid-14", "http://url-14", "Deep analysis of radar records", to_str(t_5d), to_str(t_5d), "key-14", "guid"),
        (15, 8, "guid-15", "http://url-15", "Summary of Roswell evidence", to_str(t_3d), to_str(t_3d), "key-15", "guid")
    ]
    
    conn.executemany("""
        INSERT INTO source_item (
            source_item_id, source_id, source_item_guid, canonical_url,
            title, published_at, fetched_at, ingest_dedup_key, dedup_rule
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, items)

    # Seed source item texts
    texts = [
        (1, "Full text for sighting over Texas", "default", 0, 0, "completed", None, 500, 500, 1.0, to_str(t_1d), to_str(t_1d)),
        (2, "Official briefing details here", "default", 0, 0, "completed", None, 600, 600, 1.0, to_str(t_1d), to_str(t_1d)),
        (3, "Fotocat updates blog content description", "default", 0, 0, "completed", None, 800, 800, 1.0, to_str(t_2d), to_str(t_2d)),
        (4, "Detailed analysis on Roswell myth", "default", 0, 0, "completed", None, 1200, 1200, 1.0, to_str(t_1d), to_str(t_1d)),
        (5, "Scientific study on lights in sky", "default", 0, 0, "completed", None, 1500, 1500, 1.0, to_str(t_3d), to_str(t_3d)),
        (6, "Australian government reports from archives", "default", 0, 0, "completed", None, 900, 900, 1.0, to_str(t_2d), to_str(t_2d)),
        (7, "Whistleblower states craft recovered", "default", 0, 0, "completed", None, 400, 400, 1.0, to_str(t_1d), to_str(t_1d)),
        (8, "Live stream notes on panel", "default", 0, 0, "completed", None, 700, 700, 1.0, to_str(t_1d), to_str(t_1d)),
        
        # low context
        (9, "Short", "default", 0, 0, "low_context", "too_short", 100, 5, 0.05, to_str(t_1d), to_str(t_1d)),
        
        # old items
        (10, "Old MUFON text info", "default", 0, 0, "completed", None, 400, 400, 1.0, to_str(t_10d), to_str(t_10d)),
        (11, "Old Fotocat text info", "default", 0, 0, "completed", None, 500, 500, 1.0, to_str(t_10d), to_str(t_10d)),
        (12, "Old bad ufos text info", "default", 0, 0, "completed", None, 300, 300, 1.0, to_str(t_15d), to_str(t_15d)),
        
        # remaining items within lookback
        (13, "Another Fotocat entry content info", "default", 0, 0, "completed", None, 450, 450, 1.0, to_str(t_3d), to_str(t_3d)),
        (14, "Radar signals analyzed with physical model", "default", 0, 0, "completed", None, 2000, 2000, 1.0, to_str(t_5d), to_str(t_5d)),
        (15, "Roswell panel summary", "default", 0, 0, "completed", None, 1000, 1000, 1.0, to_str(t_3d), to_str(t_3d))
    ]
    
    conn.executemany("""
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method, html_detected,
            was_truncated, text_processing_status, text_processing_reason,
            raw_text_length, sanitized_text_length, reduction_ratio, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, texts)

    # Seed classification results
    # Fields: classification_result_id, source_item_id, topic_class, classification_reason, classification_confidence, content_density, source_text_quality, primary_language_code, governmental_involvement, additional_signals, model_name, prompt_version, classified_at, created_at
    # Let's seed classification results for all completed texts (ids except 9 which is low_context)
    classifications = [
        # within lookback
        (1, "core", "sighting over Texas has core keywords", 0.95, "high", "strong", "en", 0, '{"content_timeliness": "high"}', "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d)),
        (2, "core", "official government briefing is core", 0.98, "high", "strong", "en", 1, '{"content_timeliness": "medium"}', "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d)),
        (3, "adjacent", "fotocat database list is adjacent research", 0.85, "medium", "usable", "es", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_2d), to_str(t_2d)),
        (4, "irrelevant", "bad ufos discusses debunked pop culture Roswell stuff", 0.70, "low", "poor", "en", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d)),
        (5, "adjacent", "scientific paper on unexplained lights", 0.90, "medium", "strong", "en", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_3d), to_str(t_3d)),
        (6, "core", "australian FOIA archives is core gov", 0.92, "high", "strong", "en", 1, '{"content_timeliness": "low"}', "gemini-1.5-pro", "v1.0", to_str(t_2d), to_str(t_2d)),
        (7, "core", "NewsNation whistleblower is core policy", 0.97, "medium", "strong", "en", 1, '{"content_timeliness": "high"}', "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d)),
        (8, "unknown", "classification failed or unknown", 0.50, "low", "poor", "en", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d)),
        
        # old items (10 days ago)
        (10, "core", "old mufon details", 0.96, "high", "strong", "en", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_10d), to_str(t_10d)),
        (11, "adjacent", "old fotocat details", 0.88, "medium", "usable", "es", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_10d), to_str(t_10d)),
        (12, "irrelevant", "old bad ufos review details", 0.65, "low", "poor", "en", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_15d), to_str(t_15d)),
        
        # remaining items within lookback
        (13, "adjacent", "another fotocat entry adjacent topics", 0.80, "low", "usable", "es", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_3d), to_str(t_3d)),
        (14, "core", "radar records study is core evidence", 0.99, "high", "strong", "en", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_5d), to_str(t_5d)),
        (15, "core", "Roswell panel summary core", 0.93, "medium", "usable", "en", 0, None, "gemini-1.5-pro", "v1.0", to_str(t_3d), to_str(t_3d))
    ]
    
    conn.executemany("""
        INSERT INTO classification_result (
            source_item_id, topic_class, classification_reason, classification_confidence,
            content_density, source_text_quality, primary_language_code, governmental_involvement,
            additional_signals, model_name, prompt_version, classified_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, classifications)

    # Seed curation decisions (so yield can be calculated if needed in future, but good for completeness)
    # IDs: 1, 2, 3, 5, 6, 7, 10, 11, 13, 14, 15 are approved. 4, 12, 8 are rejected.
    curations = [
        (1, "approved", "publish_summary", "valuable sighting", "operator", "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        (2, "approved", "publish_link", "official briefing", "system", "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        (3, "approved", "publish_link", "database entry", "system", "gemini-1.5-pro", "v1.0", to_str(t_2d), to_str(t_2d), to_str(t_2d)),
        (4, "rejected", "reject_discard", "irrelevant debunking", "system", "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        (5, "approved", "publish_summary", "science paper", "operator", "gemini-1.5-pro", "v1.0", to_str(t_3d), to_str(t_3d), to_str(t_3d)),
        (6, "approved", "publish_summary", "national archives", "operator", "gemini-1.5-pro", "v1.0", to_str(t_2d), to_str(t_2d), to_str(t_2d)),
        (7, "approved", "publish_summary", "NewsNation interview", "operator", "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        (8, "rejected", "reject_discard", "unknown topic rejected", "system", "gemini-1.5-pro", "v1.0", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        
        # old
        (10, "approved", "publish_summary", "old mufon summary", "operator", "gemini-1.5-pro", "v1.0", to_str(t_10d), to_str(t_10d), to_str(t_10d)),
        (11, "approved", "publish_link", "old fotocat link", "system", "gemini-1.5-pro", "v1.0", to_str(t_10d), to_str(t_10d), to_str(t_10d)),
        (12, "rejected", "reject_discard", "old bad ufos rejected", "system", "gemini-1.5-pro", "v1.0", to_str(t_15d), to_str(t_15d), to_str(t_15d)),
        
        # within lookback
        (13, "approved", "publish_link", "fotocat entries", "system", "gemini-1.5-pro", "v1.0", to_str(t_3d), to_str(t_3d), to_str(t_3d)),
        (14, "approved", "publish_summary", "radar files", "operator", "gemini-1.5-pro", "v1.0", to_str(t_5d), to_str(t_5d), to_str(t_5d)),
        (15, "approved", "publish_summary", "Roswell panel summary", "operator", "gemini-1.5-pro", "v1.0", to_str(t_3d), to_str(t_3d), to_str(t_3d))
    ]
    
    conn.executemany("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_reason,
            decision_actor, model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, curations)

    # Seed approved content records (so we can test translation and publishing later)
    # IDs: 1, 2, 3, 5, 6, 7, 10, 11, 13, 14, 15
    approved_records = [
        (1, 1, "UFO sighting over Texas", "Full body sighting over Texas", "fingerprint-1", "en", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        (2, 2, "Official briefing on UAP", "Full body briefing details here", "fingerprint-2", "en", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        (3, 3, "Fotocat updates July 2026", "Full body Fotocat updates blog", "fingerprint-3", "es", to_str(t_2d), to_str(t_2d), to_str(t_2d)),
        (4, 5, "Academic study on unexplained lights", "Full body Scientific study on lights", "fingerprint-5", "en", to_str(t_3d), to_str(t_3d), to_str(t_3d)),
        (5, 6, "Australia historical cases report", "Full body Australian archives", "fingerprint-6", "en", to_str(t_2d), to_str(t_2d), to_str(t_2d)),
        (6, 7, "NewsNation interview: whistleblowers", "Full body Whistleblower craft", "fingerprint-7", "en", to_str(t_1d), to_str(t_1d), to_str(t_1d)),
        
        # old
        (7, 10, "Old MUFON case study", "Full body old mufon details", "fingerprint-10", "en", to_str(t_10d), to_str(t_10d), to_str(t_10d)),
        (8, 11, "Old Fotocat case study", "Full body old fotocat details", "fingerprint-11", "es", to_str(t_10d), to_str(t_10d), to_str(t_10d)),
        
        # within
        (9, 13, "Another Fotocat entry", "Full body another fotocat entry", "fingerprint-13", "es", to_str(t_3d), to_str(t_3d), to_str(t_3d)),
        (10, 14, "Deep analysis of radar records", "Full body radar signals", "fingerprint-14", "en", to_str(t_5d), to_str(t_5d), to_str(t_5d)),
        (11, 15, "Summary of Roswell evidence", "Full body Roswell panel summary", "fingerprint-15", "en", to_str(t_3d), to_str(t_3d), to_str(t_3d))
    ]
    
    conn.executemany("""
        INSERT INTO approved_content_record (
            parent_content_id, source_item_id, display_title, content_body,
            content_fingerprint, content_language_code, approved_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, approved_records)

    # Seed translation outputs
    translations = [
        # parent_content_id 1 (en): translate to ja, zh
        (1, 1, "ja", "Texas UFO sighting (JA)", "JA translation body", "fingerprint-1", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_1d + datetime.timedelta(minutes=10)), to_str(t_1d + datetime.timedelta(minutes=10))),
        (1, 1, "zh", "德州UFO目擊 (ZH)", "ZH translation body", "fingerprint-1", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_1d + datetime.timedelta(minutes=12)), to_str(t_1d + datetime.timedelta(minutes=12))),
        
        # parent_content_id 2 (en): translate to ja, zh (zh failed)
        (2, 2, "ja", "UAP briefing (JA)", "JA body", "fingerprint-2", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_1d + datetime.timedelta(minutes=15)), to_str(t_1d + datetime.timedelta(minutes=15))),
        (2, 2, "zh", None, None, "fingerprint-2", "failed", 3, "gemini-1.5-pro", "v1.0", None, to_str(t_1d + datetime.timedelta(minutes=20))),

        # parent_content_id 3 (es): translate to en, ja, zh
        (3, 3, "en", "Fotocat updates (EN)", "EN body", "fingerprint-3", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_2d + datetime.timedelta(minutes=10)), to_str(t_2d + datetime.timedelta(minutes=10))),
        (3, 3, "ja", "Fotocat updates (JA)", "JA body", "fingerprint-3", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_2d + datetime.timedelta(minutes=12)), to_str(t_2d + datetime.timedelta(minutes=12))),
        (3, 3, "zh", "Fotocat更新 (ZH)", "ZH body", "fingerprint-3", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_2d + datetime.timedelta(minutes=15)), to_str(t_2d + datetime.timedelta(minutes=15))),

        # parent_content_id 4 (en): translate to ja, zh
        (4, 5, "ja", "Study (JA)", "JA body", "fingerprint-5", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_3d + datetime.timedelta(minutes=10)), to_str(t_3d + datetime.timedelta(minutes=10))),
        (4, 5, "zh", "研究 (ZH)", "ZH body", "fingerprint-5", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_3d + datetime.timedelta(minutes=15)), to_str(t_3d + datetime.timedelta(minutes=15))),

        # parent_content_id 5 (en): translate to ja, zh
        (5, 6, "ja", "Australia report (JA)", "JA body", "fingerprint-6", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_2d + datetime.timedelta(minutes=12)), to_str(t_2d + datetime.timedelta(minutes=12))),
        (5, 6, "zh", "澳洲報告 (ZH)", "ZH body", "fingerprint-6", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_2d + datetime.timedelta(minutes=18)), to_str(t_2d + datetime.timedelta(minutes=18))),

        # parent_content_id 6 (en): translate to ja, zh
        (6, 7, "ja", "NewsNation (JA)", "JA body", "fingerprint-7", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_1d + datetime.timedelta(minutes=10)), to_str(t_1d + datetime.timedelta(minutes=10))),
        (6, 7, "zh", "NewsNation (ZH)", "ZH body", "fingerprint-7", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_1d + datetime.timedelta(minutes=14)), to_str(t_1d + datetime.timedelta(minutes=14))),

        # old: parent_content_id 7 (en): translate to ja, zh
        (7, 10, "ja", "Old MUFON ja", "body ja", "fingerprint-10", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_10d), to_str(t_10d)),
        (7, 10, "zh", "Old MUFON zh", "body zh", "fingerprint-10", "completed", 0, "gemini-1.5-pro", "v1.0", to_str(t_10d), to_str(t_10d))
    ]

    conn.executemany("""
        INSERT INTO translation_output (
            parent_content_id, source_item_id, language_code, display_title, content,
            source_fingerprint, translation_status, retry_count, model_name, prompt_version,
            translated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, translations)

    # Seed publish records
    pub_records = [
        # source_item_id 1
        (1, 1, "texas-ufo-sighting", to_str(t_1d + datetime.timedelta(minutes=20)), to_str(t_1d), to_str(t_1d)),
        # source_item_id 3
        (2, 3, "fotocat-updates-july-2026", to_str(t_2d + datetime.timedelta(minutes=30)), to_str(t_2d), to_str(t_2d)),
        # source_item_id 5
        (3, 5, "academic-study-unexplained-lights", to_str(t_3d + datetime.timedelta(minutes=20)), to_str(t_3d), to_str(t_3d)),
        # source_item_id 6
        (4, 6, "australia-historical-cases-report", to_str(t_2d + datetime.timedelta(minutes=20)), to_str(t_2d), to_str(t_2d)),
        
        # old source_item_id 10
        (5, 10, "old-mufon-case-study", to_str(t_10d + datetime.timedelta(minutes=20)), to_str(t_10d), to_str(t_10d))
    ]

    conn.executemany("""
        INSERT INTO publish_record (
            publish_record_id, source_item_id, slug, first_published_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, pub_records)

    # Seed publish language statuses
    pub_lang_statuses = [
        # record 1 (texas-ufo): zh, ja published
        (1, "zh", "published", to_str(t_1d + datetime.timedelta(minutes=20)), "fingerprint-1", to_str(t_1d)),
        (1, "ja", "published", to_str(t_1d + datetime.timedelta(minutes=20)), "fingerprint-1", to_str(t_1d)),

        # record 2 (fotocat): en, zh, ja published
        (2, "en", "published", to_str(t_2d + datetime.timedelta(minutes=30)), "fingerprint-3", to_str(t_2d)),
        (2, "zh", "published", to_str(t_2d + datetime.timedelta(minutes=30)), "fingerprint-3", to_str(t_2d)),
        (2, "ja", "published", to_str(t_2d + datetime.timedelta(minutes=30)), "fingerprint-3", to_str(t_2d)),

        # record 3 (academic study): zh, ja published
        (3, "zh", "published", to_str(t_3d + datetime.timedelta(minutes=20)), "fingerprint-5", to_str(t_3d)),
        (3, "ja", "published", to_str(t_3d + datetime.timedelta(minutes=20)), "fingerprint-5", to_str(t_3d)),

        # record 4 (australia): zh, ja published
        (4, "zh", "published", to_str(t_2d + datetime.timedelta(minutes=20)), "fingerprint-6", to_str(t_2d)),
        (4, "ja", "published", to_str(t_2d + datetime.timedelta(minutes=20)), "fingerprint-6", to_str(t_2d)),

        # old: record 5
        (5, "zh", "published", to_str(t_10d + datetime.timedelta(minutes=20)), "fingerprint-10", to_str(t_10d)),
        (5, "ja", "published", to_str(t_10d + datetime.timedelta(minutes=20)), "fingerprint-10", to_str(t_10d))
    ]

    conn.executemany("""
        INSERT INTO publish_language_status (
            publish_record_id, language_code, publish_status, published_at, source_fingerprint, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, pub_lang_statuses)

    conn.commit()
    conn.close()
    print("Database seeding completed successfully.")

if __name__ == "__main__":
    db_path = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "data" / "test_sandbox.db"
    create_and_seed_db(db_path)
