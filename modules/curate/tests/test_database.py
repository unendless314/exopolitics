import json
import os
import pathlib
import sqlite3
import tempfile
import unittest

from modules.curate.src.database import (
    run_migrations,
    get_connection,
    CurationRepository,
    transaction
)

DEFAULT_CURATE_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

def create_mock_upstream_tables(db_path: pathlib.Path) -> None:
    """Helper to seed the minimal schema required for upstream ingest/classify tables."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item (
                source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                canonical_url TEXT,
                ingest_status TEXT NOT NULL CHECK (ingest_status IN ('ingested', 'draft'))
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_item_text (
                source_item_text_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                sanitized_text TEXT NOT NULL,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS classification_result (
                classification_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL UNIQUE,
                topic_class TEXT NOT NULL,
                classification_reason TEXT,
                governmental_involvement INTEGER,
                FOREIGN KEY (source_item_id) REFERENCES source_item (source_item_id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


class TestDatabaseRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        
        # Seed mock Ingest/Classify tables locally
        create_mock_upstream_tables(self.db_path)
        # Run Curate migrations
        run_migrations(self.db_path, DEFAULT_CURATE_MIGRATIONS)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_upstream_item(self, conn, item_id: int, title: str, text: str, topic_class: str, ingest_status: str = "ingested", gov_involvement: int = 0) -> None:
        cursor = conn.cursor()
        # Seed source_item
        cursor.execute("""
            INSERT INTO source_item (source_item_id, source_id, title, ingest_status)
            VALUES (?, 1, ?, ?)
        """, (item_id, title, ingest_status))
        
        # Seed source_item_text
        cursor.execute("""
            INSERT INTO source_item_text (source_item_id, sanitized_text)
            VALUES (?, ?)
        """, (item_id, text))

        # Seed classification_result
        cursor.execute("""
            INSERT INTO classification_result (source_item_id, topic_class, classification_reason, governmental_involvement)
            VALUES (?, ?, 'Reason for test', ?)
        """, (item_id, topic_class, gov_involvement))
        conn.commit()

    def test_pending_query_and_retries(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = CurationRepository(conn)

            # Seed test items:
            # 1. Item 10: Ingested, core, no curation decision -> Pending
            self.seed_upstream_item(conn, 10, "Core Pending", "Sanitized body text", "core")

            # 2. Item 20: Ingested, adjacent, curation decision failed (retry_count=1) -> Pending
            self.seed_upstream_item(conn, 20, "Adjacent Failed Retry 1", "Sanitized body text", "adjacent")
            repo.upsert_curation_decision({
                "source_item_id": 20,
                "curate_status": "failed",
                "downstream_action": None,
                "decision_reason": "Rate limited",
                "retry_count": 1,
                "model_name": "gpt-5.4-mini",
                "prompt_version": "v1"
            })

            # 3. Item 30: Ingested, adjacent, curation decision failed (retry_count=3) -> Locked, Not Pending
            self.seed_upstream_item(conn, 30, "Adjacent Failed Retry 3", "Sanitized body text", "adjacent")
            repo.upsert_curation_decision({
                "source_item_id": 30,
                "curate_status": "failed",
                "downstream_action": None,
                "decision_reason": "Repeated failures",
                "retry_count": 3,
                "model_name": "gpt-5.4-mini",
                "prompt_version": "v1"
            })

            # 4. Item 40: Ingested, core, curation decision approved -> Not Pending
            self.seed_upstream_item(conn, 40, "Core Approved", "Sanitized body text", "core")
            repo.upsert_curation_decision({
                "source_item_id": 40,
                "curate_status": "approved",
                "downstream_action": "publish_summary",
                "decision_reason": "Valid UAP report",
                "retry_count": 0,
                "model_name": "gpt-5.4-mini",
                "prompt_version": "v1"
            })

            # 5. Item 50: Ingested, irrelevant, no curation decision -> Not Pending
            self.seed_upstream_item(conn, 50, "Irrelevant Pending", "Sanitized body text", "irrelevant")

            # 6. Item 60: Draft, core, no curation decision -> Not Pending
            self.seed_upstream_item(conn, 60, "Draft Core", "Sanitized body text", "core", ingest_status="draft")

            # Get pending items
            pending = repo.get_pending_items(limit=10)
            self.assertEqual(len(pending), 2)
            
            pending_ids = [item["source_item_id"] for item in pending]
            self.assertIn(10, pending_ids)
            self.assertIn(20, pending_ids)
            self.assertNotIn(30, pending_ids)
            self.assertNotIn(40, pending_ids)
            self.assertNotIn(50, pending_ids)
            self.assertNotIn(60, pending_ids)

        finally:
            conn.close()

    def test_upserts_and_unique_constraints(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = CurationRepository(conn)
            self.seed_upstream_item(conn, 10, "Upsert Test", "Some text", "core")

            # 1. Upsert decision
            repo.upsert_curation_decision({
                "source_item_id": 10,
                "curate_status": "approved",
                "downstream_action": "publish_summary",
                "decision_reason": "High quality evidence",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "v1"
            })
            
            decision = repo.get_curation_decision(10)
            self.assertIsNotNone(decision)
            self.assertEqual(decision["curate_status"], "approved")
            self.assertEqual(decision["downstream_action"], "publish_summary")
            self.assertEqual(decision["retry_count"], 0)

            # 2. Update decision (upsert ON CONFLICT behaviour)
            repo.upsert_curation_decision({
                "source_item_id": 10,
                "curate_status": "rejected",
                "downstream_action": "reject_discard",
                "decision_reason": "Actually clickbait",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "v2"
            })

            decision_updated = repo.get_curation_decision(10)
            self.assertEqual(decision_updated["curate_status"], "rejected")
            self.assertEqual(decision_updated["downstream_action"], "reject_discard")
            self.assertEqual(decision_updated["prompt_version"], "v2")

            # 3. Upsert editor brief
            repo.upsert_editor_brief({
                "source_item_id": 10,
                "brief_goal": "Validate claim",
                "target_format": "structured_summary",
                "key_claim": "Space portal",
                "key_evidence": "Eye witness",
                "required_context": "Base involvement",
                "risk_flags": ["clickbait", "speculative"],
                "tone_guidance": "neutral, formal"
            })

            cursor = conn.cursor()
            cursor.execute("SELECT * FROM editor_brief WHERE source_item_id = 10")
            brief = cursor.fetchone()
            self.assertIsNotNone(brief)
            self.assertEqual(brief["brief_goal"], "Validate claim")
            self.assertEqual(brief["target_format"], "structured_summary")
            self.assertEqual(json.loads(brief["risk_flags"]), ["clickbait", "speculative"])

            # 4. Upsert curation output
            repo.upsert_curation_output({
                "source_item_id": 10,
                "display_title": "Clean Title",
                "summary_short": "Exemplary short summary.",
                "bullet_1": "Claim text",
                "bullet_2": "Evidence text",
                "bullet_3": "Context text"
            })

            cursor.execute("SELECT * FROM curation_output WHERE source_item_id = 10")
            output = cursor.fetchone()
            self.assertIsNotNone(output)
            self.assertEqual(output["display_title"], "Clean Title")
            self.assertEqual(output["bullet_1"], "Claim text")

            # 5. Test deletes
            repo.delete_editor_brief(10)
            cursor.execute("SELECT 1 FROM editor_brief WHERE source_item_id = 10")
            self.assertIsNone(cursor.fetchone())

            repo.delete_curation_output(10)
            cursor.execute("SELECT 1 FROM curation_output WHERE source_item_id = 10")
            self.assertIsNone(cursor.fetchone())

        finally:
            conn.close()

    def test_cascade_delete(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = CurationRepository(conn)
            self.seed_upstream_item(conn, 40, "Cascade Test", "Content text", "core")

            # Seed all curation tables for item 40
            repo.upsert_curation_decision({
                "source_item_id": 40,
                "curate_status": "approved",
                "downstream_action": "publish_summary",
                "model_name": "gpt-5.4-mini",
                "prompt_version": "v1"
            })
            repo.upsert_editor_brief({
                "source_item_id": 40,
                "brief_goal": "Validate",
                "target_format": "structured_summary",
                "risk_flags": [],
                "tone_guidance": "neutral"
            })
            repo.upsert_curation_output({
                "source_item_id": 40,
                "display_title": "Cascade",
                "summary_short": "Summary text"
            })

            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM curation_decision WHERE source_item_id = 40")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute("SELECT 1 FROM editor_brief WHERE source_item_id = 40")
            self.assertIsNotNone(cursor.fetchone())
            cursor.execute("SELECT 1 FROM curation_output WHERE source_item_id = 40")
            self.assertIsNotNone(cursor.fetchone())

            # Delete upstream source_item
            cursor.execute("DELETE FROM source_item WHERE source_item_id = 40")
            conn.commit()

            # Verify all tables cascade deleted
            cursor.execute("SELECT 1 FROM curation_decision WHERE source_item_id = 40")
            self.assertIsNone(cursor.fetchone())
            cursor.execute("SELECT 1 FROM editor_brief WHERE source_item_id = 40")
            self.assertIsNone(cursor.fetchone())
            cursor.execute("SELECT 1 FROM curation_output WHERE source_item_id = 40")
            self.assertIsNone(cursor.fetchone())
        finally:
            conn.close()

    def test_database_check_constraints(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = CurationRepository(conn)
            self.seed_upstream_item(conn, 10, "Constraint Test", "Body text", "core")

            # Constraint 1: curate_status='failed' AND downstream_action IS NOT NULL
            with self.assertRaises(sqlite3.IntegrityError):
                repo.upsert_curation_decision({
                    "source_item_id": 10,
                    "curate_status": "failed",
                    "downstream_action": "publish_link", # invalid for failed
                    "model_name": "gpt-5.4-mini",
                    "prompt_version": "v1"
                })
                conn.commit()

            # Constraint 2: curate_status='approved' AND downstream_action NOT IN ('publish_link', 'publish_summary')
            with self.assertRaises(sqlite3.IntegrityError):
                repo.upsert_curation_decision({
                    "source_item_id": 10,
                    "curate_status": "approved",
                    "downstream_action": "edit_rewrite", # invalid for approved
                    "model_name": "gpt-5.4-mini",
                    "prompt_version": "v1"
                })
                conn.commit()

            # Constraint 3: curate_status='rejected' AND downstream_action NOT IN ('edit_rewrite', 'reject_discard')
            with self.assertRaises(sqlite3.IntegrityError):
                repo.upsert_curation_decision({
                    "source_item_id": 10,
                    "curate_status": "rejected",
                    "downstream_action": "publish_link", # invalid for rejected
                    "model_name": "gpt-5.4-mini",
                    "prompt_version": "v1"
                })
                conn.commit()

        finally:
            conn.close()
