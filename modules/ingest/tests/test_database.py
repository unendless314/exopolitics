"""DDL contract, migration-runner, and SQL-splitter tests for the ingest database.

Scope (plan Phase 4, INGEST_TEST_MAINTAINABILITY_PLAN.md section 4):

- table set and index semantics locked by docs/STORAGE_SCHEMA.md sections 4 and 7
- unique / CHECK / FK constraints verified through real SQLite behavior (no mocks)
- migration idempotency and failed-migration rollback
- split_sql_statements() unit tests against its implemented contract

Schema metadata assertions lock semantics, not names (plan section 7 decision 3):
indexes are matched by table + ordered columns + uniqueness, never by the
SQLite index name.
"""

import pathlib
import re
import sqlite3
import tempfile
import unittest

from modules.ingest.src.database import (
    get_connection,
    run_migrations,
    split_sql_statements,
)

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"

NOW = "2026-01-01T00:00:00Z"

# The seven domain tables locked by STORAGE_SCHEMA.md section 4.
EXPECTED_DOMAIN_TABLES = {
    "source_item",
    "source_item_text",
    "source_item_raw",
    "source_state",
    "fetch_run",
    "fetch_attempt",
    "ingest_dedup_marker",
}

# Indexes required by STORAGE_SCHEMA.md section 7, expressed as
# (table, ordered indexed columns, unique) — names are intentionally absent.
REQUIRED_INDEXES = [
    ("source_item", ("ingest_dedup_key",), True),
    ("source_item", ("source_id",), False),
    ("source_item", ("published_at",), False),
    ("source_item_text", ("source_item_id",), True),
    ("source_item_raw", ("source_item_id",), False),
    ("source_item_raw", ("expires_at",), False),
    ("source_item_raw", ("retention_class",), False),
    ("fetch_attempt", ("fetch_run_id",), False),
    ("fetch_attempt", ("fetch_run_id", "source_id"), True),
    ("ingest_dedup_marker", ("dedup_key",), True),
    ("ingest_dedup_marker", ("source_item_id",), False),
]

# STORAGE_SCHEMA.md section 8: six application-emittable error classes plus the
# two legacy compatibility values retained in the v001 CHECK.
APPLICATION_ERROR_CLASSES = [
    "network_error",
    "timeout_error",
    "http_error_4xx",
    "http_error_5xx",
    "parse_error",
    "unexpected_error",
]
LEGACY_ERROR_CLASSES = ["validation_error", "persistence_error"]


def insert_source_item(conn, source_id=1, dedup_key="key-1", **overrides):
    values = {
        "source_id": source_id,
        "source_item_guid": "guid-1",
        "canonical_url": "https://example.com/art1",
        "title": "Title",
        "published_at": None,
        "fetched_at": NOW,
        "ingest_dedup_key": dedup_key,
        "dedup_rule": "guid",
        "ingest_status": "ingested",
    }
    values.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO source_item (
            source_id, source_item_guid, canonical_url, title, published_at,
            fetched_at, ingest_dedup_key, dedup_rule, ingest_status
        ) VALUES (
            :source_id, :source_item_guid, :canonical_url, :title, :published_at,
            :fetched_at, :ingest_dedup_key, :dedup_rule, :ingest_status
        )
        """,
        values,
    )
    return cursor.lastrowid


def insert_source_item_text(conn, source_item_id, **overrides):
    values = {
        "source_item_id": source_item_id,
        "sanitized_text": "body",
        "sanitization_method": "default_html_article",
        "html_detected": 0,
        "was_truncated": 0,
        "text_processing_status": "completed",
        "text_processing_reason": None,
        "raw_text_length": None,
        "sanitized_text_length": 4,
        "reduction_ratio": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO source_item_text (
            source_item_id, sanitized_text, sanitization_method,
            html_detected, was_truncated, text_processing_status,
            text_processing_reason, raw_text_length, sanitized_text_length,
            reduction_ratio, created_at, updated_at
        ) VALUES (
            :source_item_id, :sanitized_text, :sanitization_method,
            :html_detected, :was_truncated, :text_processing_status,
            :text_processing_reason, :raw_text_length, :sanitized_text_length,
            :reduction_ratio, :created_at, :updated_at
        )
        """,
        values,
    )
    return cursor.lastrowid


def insert_source_item_raw(conn, source_item_id, **overrides):
    values = {
        "source_item_id": source_item_id,
        "raw_payload": "<p>raw</p>",
        "retention_class": "default",
        "expires_at": None,
        "created_at": NOW,
    }
    values.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO source_item_raw (
            source_item_id, raw_payload, retention_class, expires_at, created_at
        ) VALUES (
            :source_item_id, :raw_payload, :retention_class, :expires_at, :created_at
        )
        """,
        values,
    )
    return cursor.lastrowid


def insert_dedup_marker(conn, source_item_id, dedup_key="key-1", **overrides):
    values = {
        "dedup_key": dedup_key,
        "dedup_rule": "guid",
        "source_item_id": source_item_id,
        "created_at": NOW,
    }
    values.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO ingest_dedup_marker (dedup_key, dedup_rule, source_item_id, created_at)
        VALUES (:dedup_key, :dedup_rule, :source_item_id, :created_at)
        """,
        values,
    )
    return cursor.lastrowid


def insert_source_state(conn, source_id=1, **overrides):
    values = {
        "source_id": source_id,
        "last_error_class": None,
        "health_status": "healthy",
        "updated_at": NOW,
    }
    values.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO source_state (source_id, last_error_class, health_status, updated_at)
        VALUES (:source_id, :last_error_class, :health_status, :updated_at)
        """,
        values,
    )
    return cursor.lastrowid


def insert_fetch_run(conn, **overrides):
    values = {
        "started_at": NOW,
        "run_scope": "all",
        "trigger_type": "manual",
        "run_status": "running",
        "due_source_count": 1,
    }
    values.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO fetch_run (
            started_at, run_scope, trigger_type, run_status, due_source_count
        ) VALUES (
            :started_at, :run_scope, :trigger_type, :run_status, :due_source_count
        )
        """,
        values,
    )
    return cursor.lastrowid


def insert_fetch_attempt(conn, fetch_run_id, source_id=1, **overrides):
    values = {
        "fetch_run_id": fetch_run_id,
        "source_id": source_id,
        "started_at": NOW,
        "error_class": None,
        "outcome": "success",
    }
    values.update(overrides)
    cursor = conn.execute(
        """
        INSERT INTO fetch_attempt (fetch_run_id, source_id, started_at, error_class, outcome)
        VALUES (:fetch_run_id, :source_id, :started_at, :error_class, :outcome)
        """,
        values,
    )
    return cursor.lastrowid


def index_semantics(conn, table):
    """Return [(ordered columns tuple, unique bool)] for every index on a table.

    Matches indexes by table + ordered columns + uniqueness only; SQLite index
    names are intentionally ignored (plan section 7 decision 3).
    """
    result = []
    for row in conn.execute(f"PRAGMA index_list({table})"):
        columns = tuple(
            info["name"] for info in conn.execute(f"PRAGMA index_info({row['name']})")
        )
        result.append((columns, bool(row["unique"])))
    return result


class MigratedDbTestCase(unittest.TestCase):
    """Base class: runs the real migrations into a temporary DB per test.

    Uses get_connection so PRAGMA foreign_keys is enabled exactly the way the
    production code enables it; FK tests therefore exercise the real
    enforcement path.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test.db"
        run_migrations(self.db_path, MIGRATIONS_DIR)
        self.conn = get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()


class TestTableSet(MigratedDbTestCase):
    def test_exactly_the_expected_tables_exist(self):
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        actual_tables = {row["name"] for row in rows}
        self.assertEqual(actual_tables, EXPECTED_DOMAIN_TABLES | {"schema_migrations"})

    def test_expected_tables_match_the_actual_migration_ddl(self):
        # Guard against this test drifting from the real migration code:
        # the table names declared by every *.sql migration must match the set above.
        declared = set()
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            content = sql_file.read_text(encoding="utf-8")
            declared.update(
                re.findall(
                    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
                    content,
                    flags=re.IGNORECASE,
                )
            )
        self.assertEqual(declared, EXPECTED_DOMAIN_TABLES)


class TestIndexSemantics(MigratedDbTestCase):
    def test_required_indexes_exist_with_table_columns_and_uniqueness(self):
        for table, columns, unique in REQUIRED_INDEXES:
            with self.subTest(table=table, columns=columns, unique=unique):
                available = index_semantics(self.conn, table)
                self.assertIn(
                    (columns, unique),
                    available,
                    f"{table} is missing an index on {columns} with unique={unique}; "
                    f"present indexes: {available}",
                )


class TestUniqueConstraints(MigratedDbTestCase):
    def test_duplicate_source_item_ingest_dedup_key_rejected(self):
        insert_source_item(self.conn, source_id=1, dedup_key="dup-key")
        with self.assertRaises(sqlite3.IntegrityError):
            insert_source_item(self.conn, source_id=2, dedup_key="dup-key")

    def test_duplicate_dedup_marker_key_rejected(self):
        first = insert_source_item(self.conn, source_id=1, dedup_key="key-a")
        second = insert_source_item(self.conn, source_id=1, dedup_key="key-b")
        insert_dedup_marker(self.conn, first, dedup_key="marker-dup")
        with self.assertRaises(sqlite3.IntegrityError):
            insert_dedup_marker(self.conn, second, dedup_key="marker-dup")

    def test_duplicate_fetch_attempt_run_source_pair_rejected(self):
        run_id = insert_fetch_run(self.conn)
        insert_fetch_attempt(self.conn, run_id, source_id=7)
        with self.assertRaises(sqlite3.IntegrityError):
            insert_fetch_attempt(self.conn, run_id, source_id=7)

    def test_same_source_allowed_in_different_runs(self):
        first_run = insert_fetch_run(self.conn)
        second_run = insert_fetch_run(self.conn)
        insert_fetch_attempt(self.conn, first_run, source_id=7)
        insert_fetch_attempt(self.conn, second_run, source_id=7)


class TestCheckConstraints(MigratedDbTestCase):
    def assert_value_set(self, insert, accepted, rejected):
        """Insert once per accepted value, and assert rejection per rejected value."""
        for value in accepted:
            with self.subTest(value=value, expected="accepted"):
                insert(value)
        for value in rejected:
            with self.subTest(value=value, expected="rejected"):
                with self.assertRaises(sqlite3.IntegrityError):
                    insert(value)
                self.conn.rollback()

    def test_source_item_ingest_status(self):
        self.assert_value_set(
            lambda v: insert_source_item(self.conn, dedup_key=f"k-{v}", ingest_status=v),
            accepted=["ingested"],
            rejected=["pending", "published"],
        )

    def test_source_item_dedup_rule(self):
        self.assert_value_set(
            lambda v: insert_source_item(self.conn, dedup_key=f"k-{v}", dedup_rule=v),
            accepted=["guid", "url", "tp", "fh"],
            # 'th' is marker-only per STORAGE_SCHEMA.md section 4.7, not a
            # primary identity rule for source_item.
            rejected=["th", "title_hash"],
        )

    def test_dedup_marker_dedup_rule(self):
        item_id = insert_source_item(self.conn)
        self.assert_value_set(
            lambda v: insert_dedup_marker(self.conn, item_id, dedup_key=f"m-{v}", dedup_rule=v),
            accepted=["guid", "url", "tp", "fh", "th"],
            rejected=["title_hash"],
        )

    def test_source_state_health_status(self):
        counter = iter(range(1, 100))
        self.assert_value_set(
            lambda v: insert_source_state(self.conn, source_id=next(counter), health_status=v),
            accepted=["healthy", "degraded", "quarantined"],
            rejected=["dead", "ok"],
        )

    def test_fetch_run_trigger_type(self):
        self.assert_value_set(
            lambda v: insert_fetch_run(self.conn, trigger_type=v),
            accepted=["scheduled", "manual", "recovery"],
            rejected=["cron"],
        )

    def test_fetch_run_run_status(self):
        self.assert_value_set(
            lambda v: insert_fetch_run(self.conn, run_status=v),
            accepted=["running", "success", "partial_failure", "failed"],
            rejected=["done"],
        )

    def test_fetch_attempt_outcome(self):
        run_id = insert_fetch_run(self.conn)
        counter = iter(range(1, 100))
        self.assert_value_set(
            lambda v: insert_fetch_attempt(self.conn, run_id, source_id=next(counter), outcome=v),
            accepted=["success", "failed"],
            rejected=["ok"],
        )

    def test_fetch_attempt_error_class_accepts_application_and_legacy_values(self):
        run_id = insert_fetch_run(self.conn)
        accepted = APPLICATION_ERROR_CLASSES + LEGACY_ERROR_CLASSES
        for index, value in enumerate(accepted, start=1):
            with self.subTest(error_class=value, expected="accepted"):
                insert_fetch_attempt(
                    self.conn, run_id, source_id=index, outcome="failed", error_class=value
                )
        with self.subTest(error_class="logic_error", expected="rejected"):
            with self.assertRaises(sqlite3.IntegrityError):
                insert_fetch_attempt(
                    self.conn, run_id, source_id=999, outcome="failed", error_class="logic_error"
                )
        self.conn.rollback()

    def test_source_state_last_error_class_accepts_application_and_legacy_values(self):
        accepted = APPLICATION_ERROR_CLASSES + LEGACY_ERROR_CLASSES
        for index, value in enumerate(accepted, start=1):
            with self.subTest(error_class=value, expected="accepted"):
                insert_source_state(self.conn, source_id=index, last_error_class=value)
        with self.subTest(error_class="logic_error", expected="rejected"):
            with self.assertRaises(sqlite3.IntegrityError):
                insert_source_state(self.conn, source_id=999, last_error_class="logic_error")
        self.conn.rollback()

    def test_source_item_text_processing_status(self):
        counter = iter(range(1, 100))
        self.assert_value_set(
            lambda v: insert_source_item_text(
                self.conn, _new_item(self.conn, next(counter)), text_processing_status=v
            ),
            accepted=["completed", "low_context", "failed"],
            rejected=["partial"],
        )

    def test_source_item_text_processing_reason(self):
        counter = iter(range(1, 100))
        self.assert_value_set(
            lambda v: insert_source_item_text(
                self.conn, _new_item(self.conn, next(counter)), text_processing_reason=v
            ),
            accepted=[None, "too_short", "sanitizer_exception"],
            rejected=["unknown_reason"],
        )

    def test_source_item_text_boolean_flags(self):
        counter = iter(range(1, 100))
        self.assert_value_set(
            lambda v: insert_source_item_text(
                self.conn, _new_item(self.conn, next(counter)), html_detected=v
            ),
            accepted=[0, 1],
            rejected=[2],
        )


def _new_item(conn, salt):
    return insert_source_item(conn, source_id=1000 + salt, dedup_key=f"extra-{salt}")


class TestForeignKeyActions(MigratedDbTestCase):
    def test_fk_delete_actions_in_metadata(self):
        # Semantic lock on the delete-action direction (STORAGE_SCHEMA.md section 5):
        # source_item children are restrictive, fetch_attempt cascades from fetch_run.
        expectations = {
            "source_item_text": ("source_item_id", "source_item", "RESTRICT"),
            "source_item_raw": ("source_item_id", "source_item", "RESTRICT"),
            "ingest_dedup_marker": ("source_item_id", "source_item", "RESTRICT"),
            "fetch_attempt": ("fetch_run_id", "fetch_run", "CASCADE"),
        }
        for table, (from_col, to_table, on_delete) in expectations.items():
            with self.subTest(table=table):
                rows = self.conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
                actions = {
                    (row["from"], row["table"]): row["on_delete"] for row in rows
                }
                self.assertEqual(actions.get((from_col, to_table)), on_delete)

    def test_delete_source_item_blocked_by_text_child(self):
        item_id = insert_source_item(self.conn)
        insert_source_item_text(self.conn, item_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM source_item WHERE source_item_id = ?", (item_id,))

    def test_delete_source_item_blocked_by_raw_child(self):
        item_id = insert_source_item(self.conn)
        insert_source_item_raw(self.conn, item_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM source_item WHERE source_item_id = ?", (item_id,))

    def test_delete_source_item_blocked_by_dedup_marker_child(self):
        item_id = insert_source_item(self.conn)
        insert_dedup_marker(self.conn, item_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM source_item WHERE source_item_id = ?", (item_id,))

    def test_delete_source_item_without_children_succeeds(self):
        item_id = insert_source_item(self.conn)
        self.conn.execute("DELETE FROM source_item WHERE source_item_id = ?", (item_id,))
        remaining = self.conn.execute(
            "SELECT COUNT(*) AS c FROM source_item WHERE source_item_id = ?", (item_id,)
        ).fetchone()["c"]
        self.assertEqual(remaining, 0)

    def test_delete_fetch_run_cascades_to_attempts(self):
        run_id = insert_fetch_run(self.conn)
        insert_fetch_attempt(self.conn, run_id, source_id=1)
        insert_fetch_attempt(self.conn, run_id, source_id=2)
        self.conn.execute("DELETE FROM fetch_run WHERE fetch_run_id = ?", (run_id,))
        remaining = self.conn.execute(
            "SELECT COUNT(*) AS c FROM fetch_attempt WHERE fetch_run_id = ?", (run_id,)
        ).fetchone()["c"]
        self.assertEqual(remaining, 0)

    def test_child_insert_with_missing_parent_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            insert_source_item_text(self.conn, source_item_id=424242)
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            insert_fetch_attempt(self.conn, fetch_run_id=424242)

    def test_foreign_keys_enforced_through_production_connection_helper(self):
        # Raw sqlite3 connections leave FK enforcement off; the production
        # get_connection helper must turn it on for the tests above to be real.
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            1,
        )


class TestMigrationIdempotency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.db_path = self.root / "test.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_real_migrations_do_not_reapply_on_second_run(self):
        run_migrations(self.db_path, MIGRATIONS_DIR)
        expected_names = sorted(f.name for f in MIGRATIONS_DIR.glob("*.sql"))

        conn = get_connection(self.db_path)
        insert_source_state(conn, source_id=1, health_status="degraded")
        conn.commit()
        conn.close()

        run_migrations(self.db_path, MIGRATIONS_DIR)

        conn = get_connection(self.db_path)
        names = sorted(
            row["migration_name"]
            for row in conn.execute("SELECT migration_name FROM schema_migrations")
        )
        self.assertEqual(names, expected_names)
        state = conn.execute(
            "SELECT health_status FROM source_state WHERE source_id = 1"
        ).fetchone()
        self.assertIsNotNone(state, "data must survive a migration re-run")
        self.assertEqual(state["health_status"], "degraded")
        conn.close()

    def test_applied_files_are_skipped_not_merely_tolerated(self):
        # If the runner re-applied instead of skipping, this migration would fail:
        # plain CREATE TABLE (no IF NOT EXISTS) plus a fixed-primary-key INSERT.
        migrations_dir = self.root / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "v001_guard.sql").write_text(
            "CREATE TABLE guard_table (id INTEGER PRIMARY KEY);\n"
            "INSERT INTO guard_table (id) VALUES (1);\n",
            encoding="utf-8",
        )

        run_migrations(self.db_path, migrations_dir)
        run_migrations(self.db_path, migrations_dir)

        conn = get_connection(self.db_path)
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
        self.assertEqual(count, 1)
        rows = conn.execute("SELECT id FROM guard_table").fetchall()
        self.assertEqual([row["id"] for row in rows], [1])
        conn.close()


class TestFailedMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.db_path = self.root / "test.db"
        self.migrations_dir = self.root / "migrations"
        self.migrations_dir.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _table_names(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {row["name"] for row in rows}

    def test_failed_migration_not_recorded_and_ddl_rolled_back(self):
        (self.migrations_dir / "v001_ok.sql").write_text(
            "CREATE TABLE table_a (id INTEGER PRIMARY KEY);\n"
            "INSERT INTO table_a (id) VALUES (42);\n",
            encoding="utf-8",
        )
        (self.migrations_dir / "v002_bad.sql").write_text(
            "CREATE TABLE table_b (id INTEGER PRIMARY KEY);\n"
            "THIS IS NOT VALID SQL;\n",
            encoding="utf-8",
        )

        with self.assertRaises(sqlite3.OperationalError):
            run_migrations(self.db_path, self.migrations_dir)

        conn = get_connection(self.db_path)
        recorded = {
            row["migration_name"]
            for row in conn.execute("SELECT migration_name FROM schema_migrations")
        }
        self.assertEqual(recorded, {"v001_ok.sql"})
        self.assertEqual(self._table_names(conn), {"schema_migrations", "table_a"})
        rows = conn.execute("SELECT id FROM table_a").fetchall()
        self.assertEqual([row["id"] for row in rows], [42])
        conn.close()

    def test_failed_first_migration_leaves_empty_migration_log(self):
        (self.migrations_dir / "v001_bad.sql").write_text(
            "CREATE TABLE table_x (id INTEGER PRIMARY KEY);\n"
            "THIS IS NOT VALID SQL;\n",
            encoding="utf-8",
        )

        with self.assertRaises(sqlite3.OperationalError):
            run_migrations(self.db_path, self.migrations_dir)

        conn = get_connection(self.db_path)
        # schema_migrations itself is created outside the per-file transaction.
        self.assertEqual(self._table_names(conn), {"schema_migrations"})
        count = conn.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
        self.assertEqual(count, 0)
        conn.close()


class TestSplitSqlStatements(unittest.TestCase):
    def test_single_statement(self):
        self.assertEqual(
            split_sql_statements("CREATE TABLE a (id INTEGER);"),
            ["CREATE TABLE a (id INTEGER);"],
        )

    def test_multiple_statements(self):
        sql = "CREATE TABLE a (id INTEGER);\nCREATE TABLE b (id INTEGER);\n"
        self.assertEqual(
            split_sql_statements(sql),
            ["CREATE TABLE a (id INTEGER);", "CREATE TABLE b (id INTEGER);"],
        )

    def test_multiple_statements_on_one_line(self):
        sql = "CREATE TABLE a (id INTEGER); CREATE TABLE b (id INTEGER);"
        self.assertEqual(
            split_sql_statements(sql),
            ["CREATE TABLE a (id INTEGER);", "CREATE TABLE b (id INTEGER);"],
        )

    def test_same_line_statements_are_individually_executable(self):
        # Regression coverage: a combined same-line string must not reach
        # sqlite3.Connection.execute(), which only accepts one statement.
        sql = "CREATE TABLE a (id INTEGER); CREATE TABLE b (id INTEGER);"
        conn = sqlite3.connect(":memory:")
        try:
            for statement in split_sql_statements(sql):
                conn.execute(statement)
        finally:
            conn.close()

    def test_mixed_same_line_and_multiline_statements(self):
        sql = (
            "CREATE TABLE a (id INTEGER); CREATE TABLE b (\n"
            "  id INTEGER,\n"
            "  name TEXT\n"
            ");\n"
            "INSERT INTO a VALUES (1); INSERT INTO a VALUES (2);\n"
        )
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 4)
        self.assertIn("CREATE TABLE a", statements[0])
        self.assertIn("CREATE TABLE b", statements[1])
        self.assertIn("VALUES (1)", statements[2])
        self.assertIn("VALUES (2)", statements[3])

    def test_blank_lines_and_whitespace_ignored(self):
        sql = "\n\n  CREATE TABLE a (id INTEGER);\n\n\nCREATE TABLE b (id INTEGER);\n\n"
        self.assertEqual(len(split_sql_statements(sql)), 2)

    def test_empty_input_returns_nothing(self):
        self.assertEqual(split_sql_statements("\n\n   \n"), [])

    def test_line_comment_only_input_returns_nothing(self):
        self.assertEqual(split_sql_statements("-- just a comment\n-- another\n"), [])

    def test_block_comment_only_input_returns_nothing(self):
        self.assertEqual(split_sql_statements("/* block\ncomment */"), [])

    def test_comments_stay_attached_to_following_statement(self):
        sql = "-- header\nCREATE TABLE a (id INTEGER);\n\n/* mid */\nINSERT INTO a VALUES (1);\n"
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("CREATE TABLE a (id INTEGER);", statements[0])
        self.assertIn("INSERT INTO a VALUES (1);", statements[1])

    def test_multiline_statement_joined(self):
        sql = "CREATE TABLE a (\n  id INTEGER,\n  name TEXT\n);"
        self.assertEqual(
            split_sql_statements(sql),
            ["CREATE TABLE a (\n  id INTEGER,\n  name TEXT\n);"],
        )

    def test_trailing_statement_without_semicolon_is_returned(self):
        self.assertEqual(
            split_sql_statements("CREATE TABLE a (id INTEGER)"),
            ["CREATE TABLE a (id INTEGER)"],
        )

    def test_semicolon_inside_string_literal_does_not_split(self):
        sql = "INSERT INTO a VALUES ('x;y');\nINSERT INTO a VALUES (2);"
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("'x;y'", statements[0])

    def test_double_dash_inside_string_literal_does_not_corrupt_statement(self):
        sql = "INSERT INTO a VALUES ('a--b');"
        self.assertEqual(split_sql_statements(sql), [sql])


if __name__ == "__main__":
    unittest.main()
