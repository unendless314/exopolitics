"""Ingest-to-classify handoff contract tests.

These tests build temporary databases from the REAL ingest migration
(modules/ingest/src/migrations) plus the classify migration, then verify
queue eligibility and FK behavior against that merged contract. They depend
only on the merged migration files — never on ingest's internal test helpers
— and add no runtime coupling between the modules.
"""

import pathlib
import sqlite3
import tempfile
import unittest
from typing import Optional

from modules.classify.src.database import (
    ClassificationResultRepository,
    get_connection,
    run_migrations,
)
from modules.classify.tests.helpers import (
    CLASSIFY_MIGRATIONS_DIR,
    INGEST_MIGRATIONS_DIR,
)

SEED_TS = "2026-07-30T00:00:00Z"


def seed_handoff_item(
    db_path: pathlib.Path,
    item_id: int,
    title: str,
    text: str,
    status: str = "completed",
    reason: Optional[str] = None,
) -> None:
    """Seeds one source_item + source_item_text pair on the real ingest schema."""
    conn = get_connection(db_path)
    try:
        conn.execute("""
            INSERT INTO source_item (
                source_item_id, source_id, title, ingest_dedup_key, dedup_rule, ingest_status, fetched_at
            ) VALUES (?, 1, ?, ?, 'guid', 'ingested', ?)
        """, (item_id, title, f"key-{item_id}", SEED_TS))
        conn.execute("""
            INSERT INTO source_item_text (
                source_item_id, sanitized_text, sanitization_method, html_detected, was_truncated,
                text_processing_status, text_processing_reason, sanitized_text_length, created_at, updated_at
            ) VALUES (?, ?, 'clean_v1', 0, 0, ?, ?, ?, ?, ?)
        """, (item_id, text, status, reason, len(text), SEED_TS, SEED_TS))
        conn.commit()
    finally:
        conn.close()


class TestIngestHandoff(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        run_migrations(self.db_path, INGEST_MIGRATIONS_DIR)
        run_migrations(self.db_path, CLASSIFY_MIGRATIONS_DIR)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_both_migration_sets_apply_cleanly(self) -> None:
        conn = get_connection(self.db_path)
        try:
            applied = {
                row[0] for row in conn.execute("SELECT migration_name FROM schema_migrations")
            }
            self.assertEqual(applied, {
                "v001_initial_ingest_tables.sql",
                "v001_initial_classify_tables.sql",
            })
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'classification_result'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_pending_selection_matches_handoff_contract(self) -> None:
        # Every outcome except failed / post_cleanup_empty enters the queue.
        seed_handoff_item(self.db_path, 10, "Completed", "Working text body.", status="completed")
        for idx, reason in enumerate([
            "mostly_links", "too_short", "title_heavy",
            "title_only", "template_heavy", "truncated_to_low_context",
        ], start=20):
            seed_handoff_item(self.db_path, idx, f"Low Context {reason}", "Thin text.",
                              status="low_context", reason=reason)
        seed_handoff_item(self.db_path, 26, "Empty After Cleanup", "",
                          status="low_context", reason="post_cleanup_empty")
        seed_handoff_item(self.db_path, 27, "Missing Body", "",
                          status="failed", reason="missing_body")
        seed_handoff_item(self.db_path, 28, "Sanitizer Exception", "",
                          status="failed", reason="sanitizer_exception")

        conn = get_connection(self.db_path)
        try:
            repo = ClassificationResultRepository(conn)
            pending_ids = {row["source_item_id"] for row in repo.get_pending_items(limit=50)}
            self.assertEqual(pending_ids, {10, 20, 21, 22, 23, 24, 25})
        finally:
            conn.close()

    def test_real_schema_rejects_unknown_processing_reason(self) -> None:
        # The real ingest schema CHECKs text_processing_reason; the manual mock
        # schema used by isolated unit tests does not. This pins the contract.
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                INSERT INTO source_item (
                    source_item_id, source_id, title, ingest_dedup_key, dedup_rule, ingest_status, fetched_at
                ) VALUES (99, 1, 'Bad Reason', 'key-99', 'guid', 'ingested', ?)
            """, (SEED_TS,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO source_item_text (
                        source_item_id, sanitized_text, sanitization_method, html_detected, was_truncated,
                        text_processing_status, text_processing_reason, sanitized_text_length, created_at, updated_at
                    ) VALUES (99, 'text', 'clean_v1', 0, 0, 'low_context', 'not_a_contract_reason', 4, ?, ?)
                """, (SEED_TS, SEED_TS))
                conn.commit()
        finally:
            conn.close()

    def test_ingest_status_constraint_matches_classify_assumption(self) -> None:
        # The classify pending query filters on ingest_status = 'ingested';
        # the ingest schema enforces that as the only admissible value.
        conn = get_connection(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO source_item (
                        source_item_id, source_id, title, ingest_dedup_key, dedup_rule, ingest_status, fetched_at
                    ) VALUES (98, 1, 'Bad Status', 'key-98', 'guid', 'pending', ?)
                """, (SEED_TS,))
                conn.commit()
        finally:
            conn.close()

    def test_classification_fk_cascade_with_real_schema(self) -> None:
        seed_handoff_item(self.db_path, 40, "Cascade Case", "Body text.")
        conn = get_connection(self.db_path)
        try:
            repo = ClassificationResultRepository(conn)
            repo.upsert({
                "source_item_id": 40,
                "topic_class": "core",
                "model_name": "test-model",
                "prompt_version": "v1",
            })
            conn.commit()

            # source_item_text restricts parent deletion; after removing the
            # text row, deleting source_item cascades to classification_result.
            conn.execute("DELETE FROM source_item_text WHERE source_item_id = 40")
            conn.execute("DELETE FROM source_item WHERE source_item_id = 40")
            conn.commit()

            row = conn.execute(
                "SELECT 1 FROM classification_result WHERE source_item_id = 40"
            ).fetchone()
            self.assertIsNone(row)
        finally:
            conn.close()

    def test_source_item_delete_restricted_by_text_row(self) -> None:
        seed_handoff_item(self.db_path, 41, "Restrict Case", "Body text.")
        conn = get_connection(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM source_item WHERE source_item_id = 41")
                conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
