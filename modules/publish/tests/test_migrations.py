"""
Migration runner and SQL splitter tests (plan Phase 4,
modules/publish/src/database.py).

``run_migrations()`` must be re-runnable (idempotent), must roll back a
failed migration without leaving a marker row or partial DDL, and
``split_sql_statements()`` must handle comments and multi-line or
multi-statement scripts correctly.
"""

import pathlib
import tempfile
import unittest

from modules.publish.src.database import (
    run_migrations,
    get_connection,
    split_sql_statements,
)
from modules.publish.tests import support


class TestSplitSqlStatements(unittest.TestCase):
    def test_comment_only_content_yields_no_statements(self) -> None:
        self.assertEqual([], split_sql_statements("-- only a line comment\n-- another\n"))
        self.assertEqual([], split_sql_statements("/* block comment only */\n"))

    def test_single_statement_with_comments(self) -> None:
        sql = """-- Create the table
CREATE TABLE example (id INTEGER PRIMARY KEY); -- trailing comment
"""
        statements = split_sql_statements(sql)
        self.assertEqual(1, len(statements))
        self.assertIn("CREATE TABLE example", statements[0])

    def test_multiple_statements_split(self) -> None:
        sql = """CREATE TABLE a (id INTEGER);
CREATE TABLE b (id INTEGER);
CREATE INDEX idx_b_id ON b(id);
"""
        statements = split_sql_statements(sql)
        self.assertEqual(3, len(statements))
        self.assertIn("CREATE TABLE a", statements[0])
        self.assertIn("CREATE TABLE b", statements[1])
        self.assertIn("CREATE INDEX idx_b_id", statements[2])

    def test_multiline_statement_stays_whole(self) -> None:
        sql = """CREATE TABLE multi (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    value TEXT
);
"""
        statements = split_sql_statements(sql)
        self.assertEqual(1, len(statements))
        self.assertIn("name TEXT NOT NULL", statements[0])

    def test_block_comment_inside_statement_does_not_split(self) -> None:
        sql = """CREATE TABLE c (
    id INTEGER PRIMARY KEY, /* inline block comment */
    name TEXT
);
"""
        statements = split_sql_statements(sql)
        self.assertEqual(1, len(statements))

    def test_statement_without_trailing_semicolon_kept(self) -> None:
        statements = split_sql_statements("CREATE TABLE no_semicolon (id INTEGER)")
        self.assertEqual(1, len(statements))
        self.assertIn("CREATE TABLE no_semicolon", statements[0])


class TestRunMigrations(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.temp_dir.name)
        self.db_path = base / "canonical.db"
        self.migrations_dir = base / "migrations"
        self.migrations_dir.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_migration(self, name: str, content: str) -> None:
        (self.migrations_dir / name).write_text(content, encoding="utf-8")

    def table_exists(self, table: str) -> bool:
        conn = get_connection(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def applied_markers(self) -> list:
        conn = get_connection(self.db_path)
        try:
            cursor = conn.execute("SELECT migration_name FROM schema_migrations ORDER BY migration_name")
            return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def test_real_publish_migrations_are_rerun_idempotent(self) -> None:
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)
        markers_first = self.applied_markers()
        self.assertEqual(["v001_initial_publish_tables.sql", "v002_archive_metadata.sql"], markers_first)
        self.assertTrue(self.table_exists("publish_record"))
        self.assertTrue(self.table_exists("publish_language_status"))
        self.assertTrue(self.table_exists("publish_archive_metadata"))

        # Second run: no error, no duplicate markers.
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)
        self.assertEqual(markers_first, self.applied_markers())

    def test_migrations_apply_in_sorted_order(self) -> None:
        self.write_migration("v002_second.sql", "CREATE TABLE second_table (id INTEGER);\n")
        self.write_migration("v001_first.sql", "CREATE TABLE first_table (id INTEGER);\n")
        run_migrations(self.db_path, self.migrations_dir)
        self.assertEqual(["v001_first.sql", "v002_second.sql"], self.applied_markers())
        self.assertTrue(self.table_exists("first_table"))
        self.assertTrue(self.table_exists("second_table"))

    def test_failed_migration_rolls_back_without_marker_or_partial_ddl(self) -> None:
        self.write_migration("v001_good.sql", "CREATE TABLE good_table (id INTEGER);\n")
        self.write_migration(
            "v002_bad.sql",
            "CREATE TABLE partial_table (id INTEGER);\nTHIS IS NOT VALID SQL;\n",
        )

        with self.assertRaises(Exception):
            run_migrations(self.db_path, self.migrations_dir)

        # The failed migration left neither its marker nor its partial DDL.
        self.assertEqual(["v001_good.sql"], self.applied_markers())
        self.assertTrue(self.table_exists("good_table"))
        self.assertFalse(self.table_exists("partial_table"))

        # After fixing the bad migration, a rerun completes cleanly.
        self.write_migration("v002_bad.sql", "CREATE TABLE partial_table (id INTEGER);\n")
        run_migrations(self.db_path, self.migrations_dir)
        self.assertEqual(["v001_good.sql", "v002_bad.sql"], self.applied_markers())
        self.assertTrue(self.table_exists("partial_table"))

    def test_missing_migrations_directory_is_a_no_op(self) -> None:
        missing = pathlib.Path(self.temp_dir.name) / "no_such_dir"
        run_migrations(self.db_path, missing)  # must not raise
        self.assertEqual([], self.applied_markers())


if __name__ == "__main__":
    unittest.main()
