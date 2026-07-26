"""
Phase 1 test design for the translation label-leakage refactor
(`known_issues/resolved/TRANSLATION_LABEL_LEAKAGE_REFACTOR_PLAN.md`, Phase 5 step 1;
verification matrix 7.1, analysis row), converged after the Phase 1
review.

This file covers, on a five-column schema fixture (`display_title`,
`summary_short`, `bullet_1`, `bullet_2`, `bullet_3`; the legacy
`content_body` / `content` columns are removed, mirroring
`modules/translate/docs/DATA_CONTRACT.md` section 1.4):

- the five-field formula of the existing
  `get_translation_char_volumes()` query (METRICS_CATALOG.md section
  3.1.3 Recorded Workload by language), which must be switched from
  `LENGTH(acr.display_title) + LENGTH(acr.content_body)` to the
  five-field length sum;
- non-regression: the existing completion / latency / success queries do
  not read the removed columns and must keep passing on the new schema;
- the empty-cohort convention for `get_translation_char_volumes()`
  (list queries return []).

Review ruling (see known_issues/resolved/TRANSLATION_LABEL_LEAKAGE_DISPOSAL_LIST.md):
the global Recorded / Intended workload queries and the section 4.3.4
character-share query have no report consumer yet and are deferred to a
separate case -- they will be designed and implemented only after
REPORT_CONTRACTS.md, the report schema, and the dashboard scope take them
in. Their tests were removed from this file.

Expected state before Phase 5 replaces the char_volumes formula: the
char_volumes tests FAIL with OperationalError (no such column:
acr.content_body) because the legacy query still reads the removed
column. The completion/latency non-regression test must PASS already,
proving the workload failures come from the legacy formula and not from
the fixture or the test code.

These tests intentionally do not use `xfail`: Phase 5 must make them pass
without editing this file.
"""

import sqlite3

import pytest

from modules.analysis.src.queries import translate_queries
from modules.analysis.tests.generate_mock_db import DDL_STATEMENTS

WINDOW_START = "2026-07-08T00:00:00Z"
WINDOW_END = "2026-07-15T23:59:59Z"
TARGET_LANGUAGES_JSON = '["en", "zh", "ja"]'

# Five-field target schema, mirroring
# modules/translate/docs/DATA_CONTRACT.md section 1.4. Everything else is
# reused unchanged from generate_mock_db.DDL_STATEMENTS.
FIVE_FIELD_TRANSLATE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS approved_content_record (
        parent_content_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_item_id INTEGER NOT NULL UNIQUE,
        display_title TEXT NOT NULL,
        summary_short TEXT NOT NULL,
        bullet_1 TEXT,
        bullet_2 TEXT,
        bullet_3 TEXT,
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
        summary_short TEXT,
        bullet_1 TEXT,
        bullet_2 TEXT,
        bullet_3 TEXT,
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
    """,
]


@pytest.fixture
def empty_five_field_db_conn():
    """
    Provides an empty in-memory database with the full schema, where the two
    translate tables use the five-field target shape instead of the legacy
    `content_body` / `content` columns still present in generate_mock_db.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    for stmt in DDL_STATEMENTS:
        # Skip the two legacy translate tables; replaced by the target DDL.
        if "approved_content_record" in stmt or "translation_output" in stmt:
            continue
        conn.execute(stmt)
    for stmt in FIVE_FIELD_TRANSLATE_DDL:
        conn.execute(stmt)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def five_field_db_conn(empty_five_field_db_conn):
    """
    Hand-computed cohort (SQLite LENGTH() counts characters, not bytes):

    | acr  | item | fetched_at        | action          | title | summary | b1 | b2 | b3 | total |
    | 501  | 301  | 2026-07-10 (in)   | publish_summary | 11    | 14      | 10 | 13 | 13 | 61    |
    | 502  | 302  | 2026-07-11 (in)   | publish_summary | 10    | 13      | 11 | 14 | 12 | 60    |
    | 503  | 303  | 2026-07-12 (in)   | publish_link    | 11    | 19      | -- | -- | -- | 30    |
    | 504  | 304  | 2026-07-13 (in)   | publish_summary | 11    | 14      | 8  | 11 | 9  | 53    |
    | 505  | 305  | 2026-06-01 (OUT)  | publish_summary | 13    | 16      | 8  | 11 | 9  | 57    |

    translation_output rows (tor five-field length in parentheses):
    - 501 zh completed, updated 2026-07-10 (in)  -> 3+5+4+4+4 = 20
    - 501 ja completed, updated 2026-07-10 (in)  -> 2+3+4+4+4 = 17
    - 502 zh completed, updated 2026-06-20 (OUT of event window) -> 18
    - 502 ja FAILED,    updated 2026-07-12 (in), prior content
      preserved on the failed re-run (non-NULL five fields)     -> 13
    - 503 zh completed, updated 2026-07-12 (in), bullets NULL   -> 3+5 = 8
    - 505 zh completed, updated 2026-07-14 (in), cohort OUT     -> 18
    Article 504 has no translation rows at all.
    """
    conn = empty_five_field_db_conn

    conn.executemany("""
        INSERT INTO source_item (source_item_id, source_id, title, fetched_at, ingest_dedup_key, dedup_rule)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        (301, 1, "Alpha Source", "2026-07-10T10:00:00Z", "key-301", "guid"),
        (302, 1, "Beta Source", "2026-07-11T10:00:00Z", "key-302", "guid"),
        (303, 1, "Gamma Source", "2026-07-12T10:00:00Z", "key-303", "guid"),
        (304, 1, "Delta Source", "2026-07-13T10:00:00Z", "key-304", "guid"),
        (305, 1, "Epsilon Source", "2026-06-01T10:00:00Z", "key-305", "guid"),
    ])

    conn.executemany("""
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_reason,
            decision_actor, model_name, prompt_version, curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (301, "approved", "publish_summary", "ok", "operator", "test-model", "v1.0", "2026-07-10T10:20:00Z", "2026-07-10T10:20:00Z", "2026-07-10T10:20:00Z"),
        (302, "approved", "publish_summary", "ok", "operator", "test-model", "v1.0", "2026-07-11T10:20:00Z", "2026-07-11T10:20:00Z", "2026-07-11T10:20:00Z"),
        (303, "approved", "publish_link", "ok", "operator", "test-model", "v1.0", "2026-07-12T10:20:00Z", "2026-07-12T10:20:00Z", "2026-07-12T10:20:00Z"),
        (304, "approved", "publish_summary", "ok", "operator", "test-model", "v1.0", "2026-07-13T10:20:00Z", "2026-07-13T10:20:00Z", "2026-07-13T10:20:00Z"),
        (305, "approved", "publish_summary", "ok", "operator", "test-model", "v1.0", "2026-06-01T10:20:00Z", "2026-06-01T10:20:00Z", "2026-06-01T10:20:00Z"),
    ])

    conn.executemany("""
        INSERT INTO approved_content_record (
            parent_content_id, source_item_id, display_title, summary_short,
            bullet_1, bullet_2, bullet_3,
            content_fingerprint, content_language_code, approved_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (501, 301, "Alpha Title", "Alpha summary.", "Claim one.", "Evidence two.", "Impact three.",
         "fp-301", "en", "2026-07-10T10:30:00Z", "2026-07-10T10:30:00Z", "2026-07-10T10:30:00Z"),
        # 502 approved_at predates its zh translation to keep the latency
        # fixture non-negative; window discrimination does not depend on it.
        (502, 302, "Beta Title", "Beta summary.", "Beta claim.", "Beta evidence.", "Beta impact.",
         "fp-302", "en", "2026-06-19T10:00:00Z", "2026-06-19T10:00:00Z", "2026-06-19T10:00:00Z"),
        (503, 303, "Gamma Title", "Gamma summary only.", None, None, None,
         "fp-303", "en", "2026-07-12T10:30:00Z", "2026-07-12T10:30:00Z", "2026-07-12T10:30:00Z"),
        (504, 304, "Delta Title", "Delta summary.", "D claim.", "D evidence.", "D impact.",
         "fp-304", "en", "2026-07-13T10:30:00Z", "2026-07-13T10:30:00Z", "2026-07-13T10:30:00Z"),
        (505, 305, "Epsilon Title", "Epsilon summary.", "E claim.", "E evidence.", "E impact.",
         "fp-305", "en", "2026-06-01T10:30:00Z", "2026-06-01T10:30:00Z", "2026-06-01T10:30:00Z"),
    ])

    conn.executemany("""
        INSERT INTO translation_output (
            parent_content_id, source_item_id, language_code, display_title, summary_short,
            bullet_1, bullet_2, bullet_3,
            source_fingerprint, translation_status, retry_count, model_name, prompt_version,
            translated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (501, 301, "zh", "甲標題", "甲摘要內容", "甲主張一", "甲證據二", "甲影響三",
         "fp-301", "completed", 0, "test-translator", "v1.0", "2026-07-10T11:00:00Z", "2026-07-10T11:00:00Z"),
        (501, 301, "ja", "甲題", "甲要約", "日主張一", "日證據二", "日影響三",
         "fp-301", "completed", 0, "test-translator", "v1.0", "2026-07-10T11:05:00Z", "2026-07-10T11:05:00Z"),
        (502, 302, "zh", "乙標題", "乙摘要", "乙主張一", "乙證據二", "乙影響三",
         "fp-302", "completed", 0, "test-translator", "v1.0", "2026-06-20T10:00:00Z", "2026-06-20T10:00:00Z"),
        (502, 302, "ja", "乙題", "乙要", "乙張一", "乙證二", "乙影三",
         "fp-302", "failed", 1, "test-translator", "v1.0", None, "2026-07-12T10:00:00Z"),
        (503, 303, "zh", "丙標題", "丙摘要而已", None, None, None,
         "fp-303", "completed", 0, "test-translator", "v1.0", "2026-07-12T11:00:00Z", "2026-07-12T11:00:00Z"),
        (505, 305, "zh", "戊標題", "戊摘要", "戊主張一", "戊證據二", "戊影響三",
         "fp-305", "completed", 0, "test-translator", "v1.0", "2026-07-14T10:00:00Z", "2026-07-14T10:00:00Z"),
    ])

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# METRICS_CATALOG 3.1.3 -- Recorded Workload by language (existing query)
# ---------------------------------------------------------------------------

def test_recorded_workload_by_language_five_field_formula(five_field_db_conn):
    """
    Target query (existing, formula change only):
    `get_translation_char_volumes(conn, start, end)` must replace
    `LENGTH(acr.display_title) + LENGTH(acr.content_body)` with the
    five-field length sum over `approved_content_record`. All other
    semantics stay: cohort window, one contribution per non-bypass
    translation_output row (failed rows included), grouped by tor.language_code.

    Hand-computed (each row contributes the acr five-field total):
    - zh: 501 row (61) + 502 row (60) + 503 row (30) = 151
    - ja: 501 row (61) + 502 failed row (60) = 121
    - no en row: 505 is outside the cohort window and no bypass row is seeded.
    """
    rows = translate_queries.get_translation_char_volumes(
        five_field_db_conn, WINDOW_START, WINDOW_END
    )
    volumes = {row["language_code"]: row["char_volume"] for row in rows}
    assert volumes == {"zh": 151, "ja": 121}


# ---------------------------------------------------------------------------
# Non-regression: existing completion / latency / success queries do not
# touch the removed columns and must already pass on the five-field schema.
# ---------------------------------------------------------------------------

def test_completion_latency_and_success_not_regressed_on_five_field_schema(five_field_db_conn):
    conn = five_field_db_conn

    # Event-time window rows: 501 zh, 501 ja, 502 ja (failed), 503 zh, 505 zh.
    # 502 zh is excluded (updated_at 2026-06-20, outside the event window).
    assert pytest.approx(
        translate_queries.get_overall_translation_success_rate(conn, WINDOW_START, WINDOW_END)
    ) == 0.8  # 4 completed / (4 completed + 1 failed)

    # Cohort completion: 4 in-window approved articles, 2 required
    # non-bypass targets each; only 501 has both zh and ja completed.
    assert pytest.approx(
        translate_queries.get_overall_translation_completion_rate(
            conn, WINDOW_START, WINDOW_END, TARGET_LANGUAGES_JSON
        )
    ) == 0.25

    # Non-bypass latencies (translated_at - approved_at):
    # 501 zh 1800s, 501 ja 2100s, 502 zh 86400s, 503 zh 1800s -> avg 23025.0
    assert pytest.approx(
        translate_queries.get_overall_translation_latency(conn, WINDOW_START, WINDOW_END)
    ) == 23025.0

    completion_rows = translate_queries.get_translation_completion_rates(
        conn, WINDOW_START, WINDOW_END, TARGET_LANGUAGES_JSON
    )
    completion = {row["language_code"]: row["completion_rate"] for row in completion_rows}
    assert pytest.approx(completion["zh"]) == 0.75  # 501, 502, 503 of 4 en-sourced articles
    assert pytest.approx(completion["ja"]) == 0.25  # 501 of 4
    assert completion["en"] is None  # no article with source language != en

    latency_rows = translate_queries.get_translation_latencies(conn, WINDOW_START, WINDOW_END)
    latencies = {row["language_code"]: row["avg_latency"] for row in latency_rows}
    assert set(latencies) == {"zh", "ja"}
    assert pytest.approx(latencies["zh"]) == 30000.0  # (1800 + 86400 + 1800) / 3
    assert pytest.approx(latencies["ja"]) == 2100.0

    success_stale_rows = translate_queries.get_translation_success_and_stale_rates(
        conn, WINDOW_START, WINDOW_END
    )
    success_stale = {row["language_code"]: row for row in success_stale_rows}
    assert set(success_stale) == {"zh", "ja"}
    assert pytest.approx(success_stale["zh"]["success_rate"]) == 1.0
    assert pytest.approx(success_stale["zh"]["stale_rate"]) == 0.0
    assert pytest.approx(success_stale["ja"]["success_rate"]) == 0.5
    assert pytest.approx(success_stale["ja"]["stale_rate"]) == 0.0


# ---------------------------------------------------------------------------
# Empty-cohort convention for the char_volumes query
# ---------------------------------------------------------------------------

def test_char_volumes_empty_five_field_db(empty_five_field_db_conn):
    """
    On an empty cohort `get_translation_char_volumes()` must follow the
    existing module convention: list queries return an empty list.
    """
    conn = empty_five_field_db_conn
    assert translate_queries.get_translation_char_volumes(conn, WINDOW_START, WINDOW_END) == []
