import pathlib
import sqlite3
import unittest

from modules.curate.src.database import (
    get_connection,
    run_migrations,
    split_sql_statements,
    transaction,
)
from modules.curate.tests.support import (
    CURATE_MIGRATIONS_DIR,
    make_temp_workspace,
)


class TestSplitSqlStatements(unittest.TestCase):
    def test_empty_and_whitespace_input(self):
        self.assertEqual(split_sql_statements(""), [])
        self.assertEqual(split_sql_statements("   \n\n  \t\n"), [])

    def test_comments_only_input(self):
        self.assertEqual(split_sql_statements("-- line comment\n-- another\n"), [])
        self.assertEqual(split_sql_statements("/* block comment */\n"), [])
        self.assertEqual(split_sql_statements("-- comment\n/* block */\n"), [])

    def test_single_statement(self):
        result = split_sql_statements("CREATE TABLE t (id INTEGER);")
        self.assertEqual(len(result), 1)
        self.assertIn("CREATE TABLE t", result[0])

    def test_multiple_statements(self):
        sql = (
            "CREATE TABLE t (id INTEGER);\n"
            "CREATE INDEX idx_t_id ON t(id);\n"
            "INSERT INTO t VALUES (1);\n"
        )
        self.assertEqual(len(split_sql_statements(sql)), 3)

    def test_trailing_statement_without_semicolon_is_kept(self):
        sql = "CREATE TABLE t (id INTEGER);\nINSERT INTO t VALUES (1)"
        result = split_sql_statements(sql)
        self.assertEqual(len(result), 2)
        self.assertIn("INSERT INTO t", result[1])

    def test_comments_interleaved_with_statements(self):
        sql = (
            "-- first table\n"
            "CREATE TABLE a (id INTEGER);\n"
            "\n"
            "-- second table\n"
            "CREATE TABLE b (id INTEGER);\n"
        )
        result = split_sql_statements(sql)
        self.assertEqual(len(result), 2)

    def test_active_v001_script_statement_count(self):
        content = (CURATE_MIGRATIONS_DIR / "v001_initial_curate_tables.sql").read_text(
            encoding="utf-8"
        )
        statements = split_sql_statements(content)
        # 1 PRAGMA + 3 CREATE TABLE + 4 CREATE INDEX
        self.assertEqual(len(statements), 8)
        self.assertTrue(statements[0].lstrip().upper().startswith("PRAGMA"))


class TestRunMigrations(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        self.db_path = self.workspace / "data" / "canonical.db"

    def _fetchall(self, db_path, query):
        conn = get_connection(db_path)
        try:
            return conn.execute(query).fetchall()
        finally:
            conn.close()

    def test_applies_v001_and_records_migration(self):
        run_migrations(self.db_path, CURATE_MIGRATIONS_DIR)

        tables = {
            row["name"]
            for row in self._fetchall(
                self.db_path, "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {"curation_decision", "editor_brief", "curation_output"} <= tables
        )

        applied = self._fetchall(
            self.db_path, "SELECT migration_name FROM schema_migrations"
        )
        self.assertEqual(
            [row["migration_name"] for row in applied],
            ["v001_initial_curate_tables.sql"],
        )

    def test_rerun_is_idempotent(self):
        run_migrations(self.db_path, CURATE_MIGRATIONS_DIR)
        run_migrations(self.db_path, CURATE_MIGRATIONS_DIR)  # must not fail

        applied = self._fetchall(
            self.db_path, "SELECT migration_name FROM schema_migrations"
        )
        self.assertEqual(len(applied), 1)

    def test_failed_migration_rolls_back_and_is_not_recorded(self):
        bad_dir = self.workspace / "bad_migrations"
        bad_dir.mkdir(parents=True)
        (bad_dir / "v999_bad.sql").write_text(
            "CREATE TABLE should_not_persist (id INTEGER);\n"
            "THIS IS NOT VALID SQL;\n",
            encoding="utf-8",
        )

        with self.assertRaises(sqlite3.Error):
            run_migrations(self.db_path, bad_dir)

        # The DDL inside the failed migration must be rolled back...
        tables = {
            row["name"]
            for row in self._fetchall(
                self.db_path, "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertNotIn("should_not_persist", tables)

        # ...and the migration must not be marked as applied.
        applied = self._fetchall(
            self.db_path,
            "SELECT migration_name FROM schema_migrations WHERE migration_name = 'v999_bad.sql'",
        )
        self.assertEqual(applied, [])

    def test_missing_migrations_dir_is_noop(self):
        run_migrations(self.db_path, self.workspace / "does_not_exist")
        tables = {
            row["name"]
            for row in self._fetchall(
                self.db_path, "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual(tables, {"schema_migrations"})


class TestTransaction(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        self.db_path = self.workspace / "data" / "canonical.db"
        self.conn = get_connection(self.db_path)
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE scratch (id INTEGER)")
        self.conn.commit()

    def _scratch_rows(self):
        return self.conn.execute("SELECT id FROM scratch").fetchall()

    def test_commit_persists_writes(self):
        with transaction(self.conn, commit=True):
            self.conn.execute("INSERT INTO scratch VALUES (1)")
        self.assertEqual(len(self._scratch_rows()), 1)

    def test_exception_rolls_back_and_propagates(self):
        with self.assertRaises(RuntimeError):
            with transaction(self.conn, commit=True):
                self.conn.execute("INSERT INTO scratch VALUES (2)")
                raise RuntimeError("boom")
        self.assertEqual(self._scratch_rows(), [])

    def test_dry_run_commit_false_rolls_back(self):
        with transaction(self.conn, commit=False):
            self.conn.execute("INSERT INTO scratch VALUES (3)")
        self.assertEqual(self._scratch_rows(), [])


if __name__ == "__main__":
    unittest.main()
