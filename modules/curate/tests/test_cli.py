import os
import pathlib
import sqlite3
import tempfile
import unittest
from click.testing import CliRunner

from modules.curate.src.cli import cli
from modules.curate.src.database import get_connection

class TestCliCommands(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        
        # Build mock source_item
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS source_item (
                    source_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    canonical_url TEXT,
                    ingest_status TEXT NOT NULL CHECK (ingest_status IN ('ingested'))
                );
            """)
            cursor.execute("INSERT INTO source_item (source_item_id, source_id, title, ingest_status) VALUES (1, 1, 'Test Item 1', 'ingested')")
            cursor.execute("INSERT INTO source_item (source_item_id, source_id, title, ingest_status) VALUES (2, 1, 'Test Item 2', 'ingested')")
            conn.commit()
        finally:
            conn.close()

        self.runner = CliRunner()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_withdraw_and_reapprove_workflow(self) -> None:
        # 1. Run migrate first via CLI to build curate schema
        result_migrate = self.runner.invoke(cli, ["migrate", "--db-path", str(self.db_path)])
        self.assertEqual(result_migrate.exit_code, 0)

        # Pre-seed approved curation_decision for item 1
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO curation_decision (
                    source_item_id, curate_status, downstream_action, decision_reason, decision_actor, retry_count, model_name, prompt_version, curated_at, created_at, updated_at
                ) VALUES (1, 'approved', 'publish_link', 'initial', 'system', 0, 'model', 'v1', 'curated_time', 'created_time', 'created_time')
            """)
            conn.commit()
        finally:
            conn.close()

        # 2. Try to withdraw an item that is NOT approved (item 2, which has no curation_decision)
        result_withdraw_fail = self.runner.invoke(cli, [
            "withdraw", "--db-path", str(self.db_path), "--reason", "Test fail", "2"
        ])
        self.assertNotEqual(result_withdraw_fail.exit_code, 0)
        self.assertIn("No curation decision found", result_withdraw_fail.output)

        # 3. Withdraw the approved item 1
        result_withdraw = self.runner.invoke(cli, [
            "withdraw", "--db-path", str(self.db_path), "--reason", "Manual withdraw reason", "1"
        ])
        self.assertEqual(result_withdraw.exit_code, 0)
        self.assertIn("Successfully withdrew item ID 1", result_withdraw.output)

        # Verify DB changes
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT curate_status, decision_reason, decision_actor, curated_at, updated_at FROM curation_decision WHERE source_item_id = 1")
            row = cursor.fetchone()
            self.assertEqual(row["curate_status"], "withdrawn")
            self.assertEqual(row["decision_reason"], "Manual withdraw reason")
            self.assertEqual(row["decision_actor"], "operator")
            self.assertEqual(row["curated_at"], "curated_time")  # remains unchanged
            self.assertNotEqual(row["updated_at"], "created_time")  # updated
        finally:
            conn.close()

        # 4. Try re-approving item 2 (which is not withdrawn)
        result_reapprove_fail = self.runner.invoke(cli, [
            "reapprove", "--db-path", str(self.db_path), "--reason", "Reapprove fail", "2"
        ])
        self.assertNotEqual(result_reapprove_fail.exit_code, 0)
        self.assertIn("No curation decision found", result_reapprove_fail.output)

        # 5. Reapprove the withdrawn item 1
        result_reapprove = self.runner.invoke(cli, [
            "reapprove", "--db-path", str(self.db_path), "--reason", "Manual re-approve reason", "1"
        ])
        self.assertEqual(result_reapprove.exit_code, 0)
        self.assertIn("Successfully re-approved item ID 1", result_reapprove.output)

        # Verify DB changes again
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT curate_status, decision_reason, decision_actor, curated_at, updated_at FROM curation_decision WHERE source_item_id = 1")
            row = cursor.fetchone()
            self.assertEqual(row["curate_status"], "approved")
            self.assertEqual(row["decision_reason"], "Manual re-approve reason")
            self.assertEqual(row["decision_actor"], "operator")
            self.assertEqual(row["curated_at"], "curated_time")
        finally:
            conn.close()

    def test_auto_migration_on_withdraw_and_reapprove(self) -> None:
        # Verify that calling withdraw on a DB with mock source_item,
        # but WITHOUT having run migrations manually, will auto-migrate first and succeed in finding the structure.
        result_withdraw = self.runner.invoke(cli, [
            "withdraw", "--db-path", str(self.db_path), "--reason", "Auto migrate test", "1"
        ])
        # Auto migration should run first and create the curation_decision table,
        # so we get the exit code 1 with "No curation decision found for item ID 1" instead of a SQLite schema error.
        self.assertNotEqual(result_withdraw.exit_code, 0)
        self.assertIn("No curation decision found for item ID 1", result_withdraw.output)
