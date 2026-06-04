import unittest
import tempfile
import pathlib
import sqlite3

from modules.classify.src.repository import (
    get_connection,
    run_migrations,
    ClassificationRepository,
    get_utc_now_iso8601
)

class TestRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_classify.db"
        
        # Paths to migrations
        self.ingest_migrations_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "ingest" / "src" / "migrations"
        self.classify_migrations_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

        # Apply migrations sequentially
        run_migrations(self.db_path, self.ingest_migrations_dir)
        run_migrations(self.db_path, self.classify_migrations_dir)
        
        self.conn = get_connection(self.db_path)
        self.repo = ClassificationRepository(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def _insert_test_item(self, source_item_id: int, title: str, summary: str) -> None:
        """Helper to insert a test source_item row."""
        self.conn.execute(
            """
            INSERT INTO source_item (
                source_item_id, source_id, title, summary, fetched_at, 
                ingest_dedup_key, dedup_rule, ingest_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_item_id, 
                1, 
                title, 
                summary, 
                get_utc_now_iso8601(), 
                f"key_{source_item_id}", 
                "guid", 
                "ingested", 
                get_utc_now_iso8601()
            )
        )
        self.conn.commit()

    def test_migrations_and_foreign_keys(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA foreign_keys;")
        self.assertEqual(cursor.fetchone()[0], 1)

    def test_get_pending_items(self) -> None:
        # TC-06: Database contains 1 classified item and 1 unclassified item.
        # Pending item query selects only the unclassified item.
        self._insert_test_item(1001, "Unclassified Item", "Some summary text")
        self._insert_test_item(1002, "Classified Item", "Another summary text")

        # Classify item 1002
        result_data = {
            "source_item_id": 1002,
            "topic_class": "core",
            "classification_reason": "Clear UAP mention",
            "classification_confidence": 0.95,
            "edit_candidate": 1,
            "model_name": "test-model",
            "prompt_version": "v1.0"
        }
        self.repo.save_classification_result(result_data)
        self.conn.commit()

        # Retrieve pending queue
        pending = self.repo.get_pending_items(10)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source_item_id"], 1001)
        self.assertEqual(pending[0]["title"], "Unclassified Item")

    def test_save_classification_result_and_overwrite(self) -> None:
        self._insert_test_item(2001, "Item for testing overwrite", "Summary")

        # First classification pass
        first_pass = {
            "source_item_id": 2001,
            "topic_class": "adjacent",
            "classification_reason": "Initial guess",
            "classification_confidence": 0.70,
            "edit_candidate": 0,
            "model_name": "model-a",
            "prompt_version": "v1"
        }
        self.repo.save_classification_result(first_pass)
        self.conn.commit()

        # Check DB state
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM classification_result WHERE source_item_id = ?", (2001,))
        row = dict(cursor.fetchone())
        self.assertEqual(row["topic_class"], "adjacent")
        self.assertEqual(row["classification_confidence"], 0.70)
        self.assertEqual(row["edit_candidate"], 0)
        self.assertEqual(row["model_name"], "model-a")

        # Overwrite classification pass
        second_pass = {
            "source_item_id": 2001,
            "topic_class": "core",
            "classification_reason": "Better analysis",
            "classification_confidence": 0.98,
            "edit_candidate": 1,
            "model_name": "model-b",
            "prompt_version": "v2"
        }
        self.repo.save_classification_result(second_pass)
        self.conn.commit()

        # Check DB state again - row count in table should still be 1 (upserted)
        cursor.execute("SELECT COUNT(*) FROM classification_result")
        self.assertEqual(cursor.fetchone()[0], 1)

        cursor.execute("SELECT * FROM classification_result WHERE source_item_id = ?", (2001,))
        row = dict(cursor.fetchone())
        self.assertEqual(row["topic_class"], "core")
        self.assertEqual(row["classification_confidence"], 0.98)
        self.assertEqual(row["edit_candidate"], 1)
        self.assertEqual(row["model_name"], "model-b")
        self.assertEqual(row["prompt_version"], "v2")

    def test_migration_atomicity_failure_rollback(self) -> None:
        # Verify that if a statement in a migration script fails, the entire migration rolls back
        # and nothing is registered in schema_migrations.
        with tempfile.TemporaryDirectory() as temp_mig_dir:
            bad_mig_path = pathlib.Path(temp_mig_dir) / "v003_bad_migration.sql"
            bad_mig_path.write_text("""
                CREATE TABLE should_not_exist_classify (
                    id INTEGER PRIMARY KEY
                );
                INSERT INTO non_existent_table_syntax_error VALUES (1);
            """, encoding="utf-8")

            test_db = pathlib.Path(self.temp_dir.name) / "test_atomicity_classify.db"
            # Setup upstream ingest tables first
            run_migrations(test_db, self.ingest_migrations_dir)
            
            with self.assertRaises(sqlite3.Error):
                run_migrations(test_db, pathlib.Path(temp_mig_dir))

            # Connect and verify table should_not_exist_classify was rolled back and not registered
            conn = get_connection(test_db)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_exist_classify'")
                self.assertIsNone(cursor.fetchone())
                
                cursor.execute("SELECT COUNT(*) FROM schema_migrations WHERE migration_name='v003_bad_migration.sql'")
                self.assertEqual(cursor.fetchone()[0], 0)
            finally:
                conn.close()

    def test_migration_rejects_trigger_fail_fast(self) -> None:
        # Verify that complex trigger scripts are rejected early
        with tempfile.TemporaryDirectory() as temp_mig_dir:
            trigger_mig_path = pathlib.Path(temp_mig_dir) / "v004_trigger_migration.sql"
            trigger_mig_path.write_text("""
                CREATE TRIGGER after_insert_test
                AFTER INSERT ON source_item
                BEGIN
                    SELECT 1;
                END;
            """, encoding="utf-8")

            test_db = pathlib.Path(self.temp_dir.name) / "test_trigger_classify.db"
            run_migrations(test_db, self.ingest_migrations_dir)

            with self.assertRaises(ValueError) as ctx:
                run_migrations(test_db, pathlib.Path(temp_mig_dir))
            
            self.assertIn("Complex SQL procedural statement or explicit transaction keyword", str(ctx.exception))

    def test_migration_allows_forbidden_words_inside_string_literal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_mig_dir:
            safe_mig_path = pathlib.Path(temp_mig_dir) / "v005_literal_text.sql"
            safe_mig_path.write_text("""
                CREATE TABLE literal_test (
                    id INTEGER PRIMARY KEY,
                    note TEXT DEFAULT 'create trigger begin commit rollback'
                );
            """, encoding="utf-8")

            test_db = pathlib.Path(self.temp_dir.name) / "test_literal_classify.db"
            run_migrations(test_db, self.ingest_migrations_dir)
            run_migrations(test_db, pathlib.Path(temp_mig_dir))

            conn = get_connection(test_db)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='literal_test'")
                self.assertEqual(cursor.fetchone()[0], "literal_test")
            finally:
                conn.close()

    def test_migration_rejects_bare_transaction_statements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_mig_dir:
            transaction_mig_path = pathlib.Path(temp_mig_dir) / "v006_bare_transaction.sql"
            transaction_mig_path.write_text("""
                BEGIN;
                CREATE TABLE should_not_exist_bare_tx (id INTEGER PRIMARY KEY);
                COMMIT;
            """, encoding="utf-8")

            test_db = pathlib.Path(self.temp_dir.name) / "test_bare_tx_classify.db"
            run_migrations(test_db, self.ingest_migrations_dir)

            with self.assertRaises(ValueError) as ctx:
                run_migrations(test_db, pathlib.Path(temp_mig_dir))

            self.assertIn("explicit transaction keyword 'BEGIN'", str(ctx.exception))

            conn = get_connection(test_db)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_exist_bare_tx'")
                self.assertIsNone(cursor.fetchone())
            finally:
                conn.close()

if __name__ == "__main__":
    unittest.main()
