"""Direct tests for classify-owned DDL, the migration runner, the SQL
splitter, and the transaction helper.

Database contracts are verified against real temporary SQLite databases via
SQLite metadata, not mocked IntegrityError, and index assertions lock column
semantics rather than implementation-specific index names.
"""

import pathlib
import sqlite3
import tempfile
import unittest

from modules.classify.src.database import (
    ClassificationResultRepository,
    get_connection,
    run_migrations,
    split_sql_statements,
    transaction,
)
from modules.classify.tests.helpers import (
    CLASSIFY_MIGRATIONS_DIR,
    create_mock_ingest_tables,
    seed_source_item,
)


def insert_result(conn: sqlite3.Connection, **overrides) -> None:
    """Raw INSERT of a valid classification_result row with overrides."""
    row = {
        "source_item_id": 1,
        "topic_class": "core",
        "classification_reason": "Reason.",
        "classification_confidence": 0.5,
        "content_density": "medium",
        "source_text_quality": "usable",
        "primary_language_code": "en",
        "governmental_involvement": 0,
        "additional_signals": None,
        "model_name": "test-model",
        "prompt_version": "v1",
        "classified_at": "2026-07-30T00:00:00Z",
        "created_at": "2026-07-30T00:00:00Z",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join(f":{key}" for key in row.keys())
    conn.execute(
        f"INSERT INTO classification_result ({columns}) VALUES ({placeholders})", row
    )


class TestSplitSqlStatements(unittest.TestCase):
    def test_empty_and_whitespace_inputs(self) -> None:
        self.assertEqual(split_sql_statements(""), [])
        self.assertEqual(split_sql_statements("   \n\n  "), [])

    def test_comment_only_inputs(self) -> None:
        self.assertEqual(split_sql_statements("-- just a comment\n-- another"), [])
        self.assertEqual(split_sql_statements("/* block comment */\n-- tail"), [])

    def test_single_statement_with_and_without_semicolon(self) -> None:
        self.assertEqual(len(split_sql_statements("CREATE TABLE t (a INTEGER);")), 1)
        self.assertEqual(len(split_sql_statements("CREATE TABLE t (a INTEGER)")), 1)

    def test_multiple_statements_split_in_order(self) -> None:
        statements = split_sql_statements(
            "-- header comment\n"
            "CREATE TABLE t (a INTEGER);\n"
            "-- middle comment\n"
            "INSERT INTO t VALUES (1);\n"
            "CREATE INDEX idx_t_a ON t(a);"
        )
        self.assertEqual(len(statements), 3)
        self.assertIn("CREATE TABLE t", statements[0])
        self.assertIn("INSERT INTO t", statements[1])
        self.assertIn("CREATE INDEX", statements[2])


class TestTransaction(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.conn = get_connection(self.db_path)
        self.conn.execute("CREATE TABLE demo (value TEXT)")

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def row_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM demo").fetchone()[0]

    def test_commit_persists_on_success(self) -> None:
        with transaction(self.conn):
            self.conn.execute("INSERT INTO demo VALUES ('committed')")
        self.assertEqual(self.row_count(), 1)

    def test_exception_rolls_back(self) -> None:
        with self.assertRaises(RuntimeError):
            with transaction(self.conn):
                self.conn.execute("INSERT INTO demo VALUES ('lost')")
                raise RuntimeError("boom")
        self.assertEqual(self.row_count(), 0)

    def test_commit_false_rolls_back_dry_run(self) -> None:
        with transaction(self.conn, commit=False):
            self.conn.execute("INSERT INTO demo VALUES ('dry run')")
        self.assertEqual(self.row_count(), 0)


class TestRunMigrations(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.db_path = self.root / "canonical.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def table_exists(self, table: str) -> bool:
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def applied_migrations(self) -> set:
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute("SELECT migration_name FROM schema_migrations").fetchall()
            return {row[0] for row in rows}
        finally:
            conn.close()

    def write_migration(self, migrations_dir: pathlib.Path, name: str, sql: str) -> None:
        migrations_dir.mkdir(parents=True, exist_ok=True)
        (migrations_dir / name).write_text(sql, encoding="utf-8")

    def test_rerun_is_idempotent(self) -> None:
        run_migrations(self.db_path, CLASSIFY_MIGRATIONS_DIR)
        run_migrations(self.db_path, CLASSIFY_MIGRATIONS_DIR)
        self.assertEqual(self.applied_migrations(), {"v001_initial_classify_tables.sql"})
        self.assertTrue(self.table_exists("classification_result"))

    def test_missing_migrations_dir_is_noop(self) -> None:
        run_migrations(self.db_path, self.root / "does-not-exist")
        self.assertEqual(self.applied_migrations(), set())

    def test_failed_migration_rolls_back_ddl_and_log(self) -> None:
        migrations_dir = self.root / "migrations"
        self.write_migration(migrations_dir, "v001_good.sql", "CREATE TABLE good_t (x INTEGER);")
        self.write_migration(
            migrations_dir,
            "v002_bad.sql",
            "CREATE TABLE bad_t (x INTEGER);\nTHIS IS NOT VALID SQL;",
        )

        with self.assertRaises(sqlite3.DatabaseError):
            run_migrations(self.db_path, migrations_dir)

        # The good migration committed; the failed one left neither DDL nor log row.
        self.assertEqual(self.applied_migrations(), {"v001_good.sql"})
        self.assertTrue(self.table_exists("good_t"))
        self.assertFalse(self.table_exists("bad_t"))

        # After fixing the failed migration, a rerun applies it cleanly.
        self.write_migration(migrations_dir, "v002_bad.sql", "CREATE TABLE bad_t (x INTEGER);")
        run_migrations(self.db_path, migrations_dir)
        self.assertEqual(self.applied_migrations(), {"v001_good.sql", "v002_bad.sql"})
        self.assertTrue(self.table_exists("bad_t"))


class TestClassificationResultSchema(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        create_mock_ingest_tables(self.db_path)
        run_migrations(self.db_path, CLASSIFY_MIGRATIONS_DIR)
        seed_source_item(self.db_path, 1, "Seed", "Body")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def indexed_columns(self, conn: sqlite3.Connection) -> set:
        columns = set()
        for index in conn.execute("PRAGMA index_list(classification_result)").fetchall():
            index_name = index[1]
            for info in conn.execute(f"PRAGMA index_info({index_name})").fetchall():
                columns.add(info[2])
        return columns

    def test_table_columns_match_data_contract(self) -> None:
        conn = get_connection(self.db_path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(classification_result)")}
            self.assertEqual(columns, {
                "classification_result_id", "source_item_id", "topic_class",
                "classification_reason", "classification_confidence", "content_density",
                "source_text_quality", "primary_language_code", "governmental_involvement",
                "additional_signals", "model_name", "prompt_version",
                "classified_at", "created_at",
            })
        finally:
            conn.close()

    def test_source_item_id_has_unique_index(self) -> None:
        conn = get_connection(self.db_path)
        try:
            unique_single_column_indexes = []
            for index in conn.execute("PRAGMA index_list(classification_result)").fetchall():
                if not index[2]:  # not unique
                    continue
                cols = [info[2] for info in conn.execute(f"PRAGMA index_info({index[1]})").fetchall()]
                if cols == ["source_item_id"]:
                    unique_single_column_indexes.append(index[1])
            self.assertTrue(unique_single_column_indexes)
        finally:
            conn.close()

    def test_foreign_key_delete_action_is_cascade(self) -> None:
        conn = get_connection(self.db_path)
        try:
            fk_rows = conn.execute("PRAGMA foreign_key_list(classification_result)").fetchall()
            self.assertEqual(len(fk_rows), 1)
            fk = fk_rows[0]
            self.assertEqual(fk[2], "source_item")       # referenced table
            self.assertEqual(fk[4], "source_item_id")    # referenced column
            self.assertEqual(fk[6], "CASCADE")           # ON DELETE action
        finally:
            conn.close()

    def test_query_columns_are_indexed(self) -> None:
        conn = get_connection(self.db_path)
        try:
            columns = self.indexed_columns(conn)
            self.assertIn("topic_class", columns)
            self.assertIn("source_item_id", columns)
        finally:
            conn.close()

    def test_check_constraints_reject_invalid_values(self) -> None:
        invalid_rows = [
            {"topic_class": "invalid-topic-class"},
            {"classification_confidence": 1.5},
            {"classification_confidence": -0.1},
            {"content_density": "dense"},
            {"source_text_quality": "great"},
            {"governmental_involvement": 2},
        ]
        conn = get_connection(self.db_path)
        try:
            for overrides in invalid_rows:
                with self.subTest(**overrides), self.assertRaises(sqlite3.IntegrityError):
                    insert_result(conn, **overrides)
                    conn.commit()
                conn.rollback()
        finally:
            conn.close()

    def test_duplicate_source_item_id_rejected(self) -> None:
        conn = get_connection(self.db_path)
        try:
            insert_result(conn)
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                insert_result(conn)
                conn.commit()
        finally:
            conn.close()

    def test_upsert_preserves_surrogate_key(self) -> None:
        conn = get_connection(self.db_path)
        try:
            repo = ClassificationResultRepository(conn)
            base = {
                "source_item_id": 1,
                "topic_class": "core",
                "model_name": "test-model",
                "prompt_version": "v1",
            }
            repo.upsert(base)
            conn.commit()
            first_id = conn.execute(
                "SELECT classification_result_id FROM classification_result WHERE source_item_id = 1"
            ).fetchone()[0]

            repo.upsert({**base, "topic_class": "adjacent", "prompt_version": "v2"})
            conn.commit()

            rows = conn.execute(
                "SELECT classification_result_id, topic_class FROM classification_result WHERE source_item_id = 1"
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], first_id)
            self.assertEqual(rows[0][1], "adjacent")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
