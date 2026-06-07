import unittest
import tempfile
import pathlib
import sqlite3
from modules.ingest.src.database import (
    get_connection,
    transaction,
    run_migrations,
    split_sql_statements,
    get_utc_now_iso8601,
    SourceStateRepository,
    FetchRunRepository,
    FetchAttemptRepository,
    SourceItemRepository,
    DedupMarkerRepository
)

class TestDatabaseStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_ingest.db"
        self.migrations_dir = pathlib.Path(__file__).parent.parent / "src" / "migrations"

        # Apply migrations to the test database
        run_migrations(self.db_path, self.migrations_dir)
        self.conn = get_connection(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_pragma_foreign_keys_enabled(self) -> None:
        # Check that foreign keys are enabled (returns 1)
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA foreign_keys;")
        fk_status = cursor.fetchone()[0]
        self.assertEqual(fk_status, 1)

    def test_pragma_foreign_keys_violating_fails(self) -> None:
        # Inserting a fetch_attempt with a non-existent fetch_run_id should raise IntegrityError
        # because fetch_run_id is a foreign key to fetch_run table
        attempt_repo = FetchAttemptRepository(self.conn)
        attempt_data = {
            "fetch_run_id": 9999,  # Does not exist
            "source_id": 1,
            "attempt_started_at": get_utc_now_iso8601(),
            "outcome": "success"
        }
        
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.conn):
                attempt_repo.insert(attempt_data)

    def test_migration_reentrancy(self) -> None:
        # Running migrations again on the same DB should be completely safe and re-entrant
        try:
            run_migrations(self.db_path, self.migrations_dir)
        except Exception as e:
            self.fail(f"Re-running migrations raised an exception: {e}")

        # Check that schema_migrations has recorded the applied migration
        cursor = self.conn.cursor()
        cursor.execute("SELECT migration_name, applied_at FROM schema_migrations")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["migration_name"], "v001_initial_ingest_tables.sql")

    def test_migration_atomicity_failure_rollback(self) -> None:
        # Verify that if a DDL script fails midway, the DDL is rolled back
        # and the migration is NOT recorded in schema_migrations.
        with tempfile.TemporaryDirectory() as temp_mig_dir:
            bad_mig_path = pathlib.Path(temp_mig_dir) / "v002_bad_migration.sql"
            # Write a SQL script that creates a table, but contains a syntax error at the end
            bad_mig_path.write_text("""
                CREATE TABLE should_not_exist (
                    id INTEGER PRIMARY KEY
                );
                INSERT INTO non_existent_table_syntax_error VALUES (1);
            """, encoding="utf-8")

            # Running migrations should raise an exception due to syntax/operational error
            test_db = pathlib.Path(self.temp_dir.name) / "test_atomicity.db"
            
            with self.assertRaises(sqlite3.OperationalError):
                run_migrations(test_db, pathlib.Path(temp_mig_dir))

            # Connect and verify:
            # 1. The table 'should_not_exist' DOES NOT exist in the database (rolled back)
            # 2. 'schema_migrations' is empty or doesn't have the bad record
            conn = get_connection(test_db)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_exist'")
                self.assertIsNone(cursor.fetchone(), "Table 'should_not_exist' should have been rolled back!")
                
                cursor.execute("SELECT COUNT(*) FROM schema_migrations WHERE migration_name='v002_bad_migration.sql'")
                count = cursor.fetchone()[0]
                self.assertEqual(count, 0, "Bad migration should not be registered in schema_migrations!")
            finally:
                conn.close()

    def test_split_sql_statements_ignores_comment_only_chunks(self) -> None:
        sql_content = """
        -- comment-only prelude;
        /* block comment with ; semicolon */

        CREATE TABLE example_table (
            id INTEGER PRIMARY KEY
        );

        -- trailing comment-only chunk;
        """

        statements = split_sql_statements(sql_content)

        self.assertEqual(len(statements), 1)
        self.assertIn("CREATE TABLE example_table", statements[0])

    def test_transaction_commit_on_success(self) -> None:
        run_repo = FetchRunRepository(self.conn)
        
        # Insert a run inside a successful transaction block
        with transaction(self.conn):
            run_id = run_repo.create(run_scope="all", trigger_type="scheduled", due_source_count=5)
            
        # Verify the record exists in the database with status 'running'
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM fetch_run WHERE fetch_run_id = ?", (run_id,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["run_scope"], "all")
        self.assertEqual(row["run_status"], "running")

    def test_transaction_rollback_on_error(self) -> None:
        run_repo = FetchRunRepository(self.conn)
        
        # Attempt to insert, but raise an exception inside the block
        try:
            with transaction(self.conn):
                run_id = run_repo.create(run_scope="all", trigger_type="scheduled", due_source_count=5)
                raise ValueError("Simulated operational crash")
        except ValueError:
            pass # Caught expected error

        # Verify the record was rolled back and DOES NOT exist in the database
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM fetch_run")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_source_state_repository_upsert(self) -> None:
        state_repo = SourceStateRepository(self.conn)
        
        # Test Initial Insert
        state_data = {
            "last_fetch_at": "2026-06-01T10:00:00Z",
            "last_success_at": "2026-06-01T10:00:00Z",
            "last_http_status": 200,
            "consecutive_failures": 0,
            "health_status": "healthy"
        }
        
        with transaction(self.conn):
            state_repo.upsert(source_id=1, state_data=state_data)
            
        row = state_repo.get(source_id=1)
        self.assertIsNotNone(row)
        self.assertEqual(row["last_http_status"], 200)
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["health_status"], "healthy")

        # Test Update / Upsert
        updated_data = {
            "last_fetch_at": "2026-06-01T11:00:00Z",
            "last_success_at": "2026-06-01T10:00:00Z",
            "last_http_status": 500,
            "consecutive_failures": 1,
            "last_error_class": "http_error_5xx",
            "health_status": "degraded"
        }
        
        with transaction(self.conn):
            state_repo.upsert(source_id=1, state_data=updated_data)
            
        row = state_repo.get(source_id=1)
        self.assertIsNotNone(row)
        self.assertEqual(row["last_http_status"], 500)
        self.assertEqual(row["consecutive_failures"], 1)
        self.assertEqual(row["last_error_class"], "http_error_5xx")
        self.assertEqual(row["health_status"], "degraded")

    def test_source_item_and_dedup_marker_cascade(self) -> None:
        item_repo = SourceItemRepository(self.conn)
        dedup_repo = DedupMarkerRepository(self.conn)

        # 1. Insert an Item
        item_data = {
            "source_id": 5,
            "source_item_guid": "uap-guid-123",
            "canonical_url": "https://example.com/uap-news",
            "title": "Unidentified Object Spotted Over Pacific",
            "ingest_dedup_key": "guid:uap-guid-123",
            "dedup_rule": "guid"
        }
        
        with transaction(self.conn):
            item_id = item_repo.insert(item_data)

        # 2. Insert Dedup Marker referencing item_id
        with transaction(self.conn):
            dedup_repo.insert(dedup_key="guid:uap-guid-123", dedup_rule="guid", source_item_id=item_id)

        # 3. Verify deduplication markers
        self.assertTrue(dedup_repo.exists("guid:uap-guid-123"))
        self.assertFalse(dedup_repo.exists("guid:not-exists"))

        # 4. Verify cascade delete: deleting source_item should cascade delete dedup marker
        with transaction(self.conn):
            self.conn.execute("DELETE FROM source_item WHERE source_item_id = ?", (item_id,))

        self.assertFalse(dedup_repo.exists("guid:uap-guid-123"))

    def test_transaction_safety_item_and_marker_insert_failure(self) -> None:
        item_repo = SourceItemRepository(self.conn)
        dedup_repo = DedupMarkerRepository(self.conn)

        # First, insert an item and register its dedup marker successfully
        item1_data = {
            "source_id": 5,
            "source_item_guid": "uap-guid-999",
            "canonical_url": "https://example.com/uap-news-999",
            "title": "First Unidentified Object",
            "ingest_dedup_key": "guid:5:uap-guid-999",
            "dedup_rule": "guid"
        }
        with transaction(self.conn):
            item1_id = item_repo.insert(item1_data)
            dedup_repo.insert(dedup_key="guid:5:uap-guid-999", dedup_rule="guid", source_item_id=item1_id)

        # Now, try to insert another item, but force a duplicate dedup_key collision
        item2_data = {
            "source_id": 5,
            "source_item_guid": "uap-guid-duplicate",
            "canonical_url": "https://example.com/uap-news-dup",
            "title": "Second Object with Colliding Key",
            "ingest_dedup_key": "guid:5:uap-guid-999",  # Collides with item1's dedup key!
            "dedup_rule": "guid"
        }

        # Attempt to insert within a single transaction block
        with self.assertRaises(sqlite3.IntegrityError):
            with transaction(self.conn):
                # The item insertion itself succeeds...
                item2_id = item_repo.insert(item2_data)
                # ...but the dedup marker insertion will raise IntegrityError due to UNIQUE constraint!
                dedup_repo.insert(dedup_key="guid:5:uap-guid-999", dedup_rule="guid", source_item_id=item2_id)

        # Verify that because of the exception, the entire transaction rolled back:
        # The second item ('Second Object with Colliding Key') MUST NOT exist in the database!
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM source_item WHERE title = 'Second Object with Colliding Key'")
        count = cursor.fetchone()[0]
        self.assertEqual(count, 0, "The second item should have been rolled back and not persisted!")

if __name__ == "__main__":
    unittest.main()
