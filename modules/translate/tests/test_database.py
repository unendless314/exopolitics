"""Database utility and translate-owned DDL contract tests.

TRANSLATE_TEST_MAINTAINABILITY_PLAN section 3.6 and Phase 3 work items 5-6,
covering modules/translate/src/database.py:

- split_sql_statements(): empty, whitespace-only and comments-only input;
  single, multiple, multi-line and unterminated-final-statement scripts.
  Split output must stay executable against a real temp DB.
- run_migrations(): idempotency against the real migrations dir
  (support.DEFAULT_TRANSLATE_MIGRATIONS) and failure atomicity (no
  schema_migrations row for the failed migration, partial DDL rolled back).
- transaction(): commit persists, exceptions roll back and re-raise,
  commit=False rolls back without raising.
- translation_output DDL enforced by the real migration
  v001_initial_translate_tables.sql: UNIQUE(parent_content_id, language_code),
  CHECK constraints, ON DELETE CASCADE from approved_content_record, and index
  semantics via PRAGMA metadata (columns and uniqueness, never index names).
- TranslationRepository direct contracts: upsert_translation_output,
  detect_and_mark_stale and the get_pending_translation_tasks eligibility
  matrix.

Every database lives under tempfile.TemporaryDirectory. No workspace
canonical DB, no .env reads, no network.
"""

import pathlib
import sqlite3
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Tuple

from modules.translate.src.database import (
    TranslationRepository,
    get_connection,
    run_migrations,
    split_sql_statements,
    transaction,
)
from modules.translate.tests import support

# zh payloads contain CJK, ja payloads contain Hiragana/Katakana (runner-side
# script validation contract).
_TRANSLATED_CONTENT = {
    "zh": ("翻譯標題", "翻譯後的中文摘要內容。", "第一要點內容。", "第二要點內容。", "第三要點內容。"),
    "ja": ("翻訳タイトル", "翻訳された要約の内容です。", "第一の要点です。", "第二の要点です。", "第三の要点です。"),
}


def _translation_kwargs(
    *,
    parent_content_id: int,
    status: str,
    retry_count: int,
    language_code: str = "zh",
    source_fingerprint: Optional[str] = None,
    model_name: str = "gpt-5.4-mini",
    prompt_version: str = "translator_v2",
) -> Dict[str, Any]:
    """Keyword payload for support.seed_translation_row.

    Status and retry_count stay explicit preconditions; the fingerprint and
    model/prompt default to "matches the approved record / running config".
    """
    title, summary, bullet_1, bullet_2, bullet_3 = _TRANSLATED_CONTENT[language_code]
    return {
        "parent_content_id": parent_content_id,
        "source_item_id": 100 + parent_content_id,
        "language_code": language_code,
        "display_title": title,
        "summary_short": summary,
        "bullet_1": bullet_1,
        "bullet_2": bullet_2,
        "bullet_3": bullet_3,
        "source_fingerprint": (
            source_fingerprint
            if source_fingerprint is not None
            else f"fp_{parent_content_id}"
        ),
        "status": status,
        "retry_count": retry_count,
        "model_name": model_name,
        "prompt_version": prompt_version,
    }


# ---------------------------------------------------------------------------
# split_sql_statements (plan section 3.6, Phase 3 item 5)
# ---------------------------------------------------------------------------

class TestSplitSqlStatements(unittest.TestCase):
    """SQL splitter contract: blank and comment-only scripts yield nothing;
    statement boundaries follow sqlite3.complete_statement."""

    def assert_statements_execute(
        self, statements: List[str], expected_tables: set
    ) -> None:
        """Executes the split statements against a real temp DB."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = pathlib.Path(tmp) / "split_test.db"
            conn = get_connection(db_path)
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.commit()
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            finally:
                conn.close()
        for table in expected_tables:
            self.assertIn(table, tables)

    def test_empty_string_yields_no_statements(self) -> None:
        self.assertEqual(split_sql_statements(""), [])

    def test_whitespace_only_yields_no_statements(self) -> None:
        self.assertEqual(split_sql_statements("   \n\t\n  \n"), [])

    def test_comments_only_yield_no_statements(self) -> None:
        cases = {
            "line_comments": "-- first comment\n-- second comment\n",
            "line_comment_with_sql": "-- CREATE TABLE ignored_a (id INTEGER);\n",
            "block_comment": "/* a block comment\n   spanning lines */\n",
            "block_comment_with_sql": "/* CREATE TABLE ignored_b (id INTEGER); */\n",
            "mixed_comments": "-- head\n/* body; */\n-- tail;\n",
        }
        for label, sql in cases.items():
            with self.subTest(kind=label):
                self.assertEqual(split_sql_statements(sql), [])

    def test_single_statement(self) -> None:
        sql = "CREATE TABLE single_stmt (id INTEGER);"
        statements = split_sql_statements(sql)
        self.assertEqual(statements, [sql])
        self.assert_statements_execute(statements, {"single_stmt"})

    def test_multiple_statements(self) -> None:
        sql = (
            "CREATE TABLE multi_a (id INTEGER);\n"
            "CREATE TABLE multi_b (id INTEGER);\n"
        )
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assert_statements_execute(statements, {"multi_a", "multi_b"})

    def test_statement_spanning_multiple_lines(self) -> None:
        sql = (
            "CREATE TABLE spanning (\n"
            "    id INTEGER PRIMARY KEY,\n"
            "    name TEXT NOT NULL\n"
            ");"
        )
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 1)
        self.assert_statements_execute(statements, {"spanning"})

    def test_final_statement_without_trailing_semicolon(self) -> None:
        sql = (
            "CREATE TABLE terminated (id INTEGER);\n"
            "CREATE TABLE unterminated (id INTEGER)"
        )
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertTrue(statements[0].rstrip().endswith(";"))
        self.assertFalse(statements[1].rstrip().endswith(";"))
        self.assert_statements_execute(statements, {"terminated", "unterminated"})


# ---------------------------------------------------------------------------
# run_migrations (plan section 3.6, Phase 3 item 5)
# ---------------------------------------------------------------------------

class TestRunMigrations(unittest.TestCase):
    """Migration runner: re-run idempotency against the real migrations dir
    and failure atomicity verified through real SQLite state."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = pathlib.Path(self.temp_dir.name)

    @staticmethod
    def _fetch_migration_rows(db_path: pathlib.Path) -> List[Tuple[str, str]]:
        conn = get_connection(db_path)
        try:
            return [
                (row["migration_name"], row["applied_at"])
                for row in conn.execute(
                    "SELECT migration_name, applied_at FROM schema_migrations "
                    "ORDER BY migration_name"
                ).fetchall()
            ]
        finally:
            conn.close()

    @staticmethod
    def _fetch_schema_snapshot(db_path: pathlib.Path) -> List[Tuple[Any, ...]]:
        conn = get_connection(db_path)
        try:
            return [
                tuple(row)
                for row in conn.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "ORDER BY type, name"
                ).fetchall()
            ]
        finally:
            conn.close()

    def test_real_migrations_are_idempotent(self) -> None:
        db_path = self.workspace / "data" / "canonical.db"
        support.create_minimal_upstream_tables(db_path)
        migrations_dir = support.DEFAULT_TRANSLATE_MIGRATIONS
        expected_names = sorted(p.name for p in migrations_dir.glob("*.sql"))
        self.assertTrue(expected_names, "real translate migrations are missing")

        run_migrations(db_path, migrations_dir)
        first_rows = self._fetch_migration_rows(db_path)
        # Exactly one schema_migrations row per .sql file.
        self.assertEqual([name for name, _ in first_rows], expected_names)
        schema_before = self._fetch_schema_snapshot(db_path)
        table_names = {r[1] for r in schema_before if r[0] == "table"}
        self.assertIn("approved_content_record", table_names)
        self.assertIn("translation_output", table_names)

        run_migrations(db_path, migrations_dir)
        # The second run changes nothing: same log rows (including applied_at)
        # and identical schema.
        self.assertEqual(self._fetch_migration_rows(db_path), first_rows)
        self.assertEqual(self._fetch_schema_snapshot(db_path), schema_before)

    def test_failed_migration_rolls_back_and_leaves_no_log(self) -> None:
        migrations_dir = self.workspace / "broken_migrations"
        migrations_dir.mkdir(parents=True)
        (migrations_dir / "v999_broken.sql").write_text(
            "CREATE TABLE rolled_back_table (id INTEGER);\n"
            "THIS IS NOT VALID SQL;\n",
            encoding="utf-8",
        )
        db_path = self.workspace / "data" / "canonical.db"

        with self.assertRaises(sqlite3.OperationalError):
            run_migrations(db_path, migrations_dir)

        conn = get_connection(db_path)
        try:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            # The valid CREATE TABLE from the failed migration was rolled back.
            self.assertNotIn("rolled_back_table", tables)
            # The bookkeeping table is created and committed before the
            # migration loop, so it is expected to persist...
            self.assertIn("schema_migrations", tables)
            # ...but it must not log the failed migration.
            logged = conn.execute(
                "SELECT migration_name FROM schema_migrations"
            ).fetchall()
            self.assertEqual(logged, [])
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# transaction (plan section 3.6, Phase 3 item 5)
# ---------------------------------------------------------------------------

class TestTransaction(unittest.TestCase):
    """transaction(): strict BEGIN IMMEDIATE boundary with commit, exception
    rollback and commit=False dry-run rollback."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = support.build_temp_workspace(
            pathlib.Path(self.temp_dir.name)
        )

    def open_connection(self) -> sqlite3.Connection:
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def _count_titles(self, conn: sqlite3.Connection, title: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM source_item WHERE title = ?", (title,)
        ).fetchone()[0]

    def test_commit_persists_writes(self) -> None:
        conn = self.open_connection()
        with transaction(conn):
            conn.execute(
                "INSERT INTO source_item (source_id, title, ingest_status) "
                "VALUES (1, 'Committed Title', 'ingested')"
            )
        # Visible from a separate connection: the write was really committed.
        check = self.open_connection()
        self.assertEqual(self._count_titles(check, "Committed Title"), 1)

    def test_exception_rolls_back_and_reraises(self) -> None:
        conn = self.open_connection()
        with self.assertRaises(ValueError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO source_item (source_id, title, ingest_status) "
                    "VALUES (1, 'Rolled Back Title', 'ingested')"
                )
                raise ValueError("simulated failure")
        self.assertEqual(self._count_titles(conn, "Rolled Back Title"), 0)

    def test_commit_false_rolls_back_without_raising(self) -> None:
        conn = self.open_connection()
        with transaction(conn, commit=False):
            conn.execute(
                "INSERT INTO source_item (source_id, title, ingest_status) "
                "VALUES (1, 'Dry Run Title', 'ingested')"
            )
        self.assertEqual(self._count_titles(conn, "Dry Run Title"), 0)


# ---------------------------------------------------------------------------
# Shared base: temp DB with minimal upstream fixture + real translate DDL
# ---------------------------------------------------------------------------

class _TranslateSchemaBase(unittest.TestCase):
    """Builds the temp workspace DB via support helpers and seeds approved
    records with explicit fingerprints."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = support.build_temp_workspace(
            pathlib.Path(self.temp_dir.name)
        )

    def open_connection(self) -> sqlite3.Connection:
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def seed_record(
        self,
        conn: sqlite3.Connection,
        *,
        parent_content_id: int,
        fingerprint: Optional[str] = None,
        approved_at: str = "2026-06-20T12:00:00Z",
    ) -> None:
        support.seed_approved_record(
            conn,
            parent_content_id=parent_content_id,
            source_item_id=100 + parent_content_id,
            display_title=f"English Title {parent_content_id}",
            summary_short=f"English summary content {parent_content_id}.",
            bullet_1="Claim content.",
            bullet_2="Evidence content.",
            bullet_3="Impact content.",
            content_fingerprint=fingerprint or f"fp_{parent_content_id}",
            content_language_code="en",
            approved_at=approved_at,
        )


# ---------------------------------------------------------------------------
# Translate-owned DDL constraints (plan Phase 3 item 6)
# ---------------------------------------------------------------------------

class TestTranslateDdlConstraints(_TranslateSchemaBase):
    """UNIQUE / CHECK / FK cascade / index semantics of translation_output as
    created by the real v001 migration, verified via SQLite metadata and real
    write behavior."""

    def test_unique_parent_language_pair_rejects_duplicates(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1)
        kwargs = _translation_kwargs(
            parent_content_id=1, status="completed", retry_count=0
        )
        support.seed_translation_row(conn, **kwargs)
        with self.assertRaises(sqlite3.IntegrityError):
            support.seed_translation_row(conn, **kwargs)

    def test_translation_status_check_rejects_invalid_value(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1)
        with self.assertRaises(sqlite3.IntegrityError):
            support.seed_translation_row(
                conn,
                **_translation_kwargs(
                    parent_content_id=1, status="not_a_status", retry_count=0
                ),
            )

    def test_retry_count_check_rejects_negative_values(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1)
        with self.assertRaises(sqlite3.IntegrityError):
            support.seed_translation_row(
                conn,
                **_translation_kwargs(
                    parent_content_id=1, status="pending", retry_count=-1
                ),
            )

    def test_approved_record_delete_cascades_translation_rows(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1)
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1, status="completed", retry_count=0
            ),
        )
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1,
                language_code="ja",
                status="completed",
                retry_count=0,
            ),
        )
        self.assertIsNotNone(
            support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="zh"
            )
        )
        self.assertIsNotNone(
            support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="ja"
            )
        )

        conn.execute(
            "DELETE FROM approved_content_record WHERE parent_content_id = 1"
        )
        conn.commit()

        self.assertIsNone(
            support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="zh"
            )
        )
        self.assertIsNone(
            support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="ja"
            )
        )
        remaining = conn.execute(
            "SELECT COUNT(*) FROM translation_output"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_index_semantics_match_ddl_contract(self) -> None:
        # Columns and uniqueness are asserted, never index names (plan Phase 3
        # item 6).
        conn = self.open_connection()
        shapes = []
        for idx in conn.execute(
            "PRAGMA index_list('translation_output')"
        ).fetchall():
            columns = tuple(
                info["name"]
                for info in conn.execute(
                    f"PRAGMA index_info({idx['name']})"
                ).fetchall()
            )
            shapes.append((columns, bool(idx["unique"])))
        self.assertIn((("parent_content_id", "language_code"), True), shapes)
        self.assertIn((("translation_status",), False), shapes)


# ---------------------------------------------------------------------------
# TranslationRepository.upsert_translation_output (plan Phase 3 item 5)
# ---------------------------------------------------------------------------

class TestUpsertTranslationOutput(_TranslateSchemaBase):
    """Upsert on (parent_content_id, language_code): a second upsert updates
    the existing row instead of inserting a duplicate."""

    def test_upsert_updates_existing_row_without_duplicating(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1, fingerprint="fp_1")
        repo = TranslationRepository(conn)

        base: Dict[str, Any] = {
            "parent_content_id": 1,
            "source_item_id": 101,
            "language_code": "zh",
            "display_title": "原始標題",
            "summary_short": "原始中文摘要內容。",
            "bullet_1": "原始第一要點。",
            "bullet_2": "原始第二要點。",
            "bullet_3": "原始第三要點。",
            "source_fingerprint": "fp_1",
            "translation_status": "completed",
            "retry_count": 0,
            "model_name": "gpt-5.4-mini",
            "prompt_version": "translator_v2",
            "translated_at": "2026-06-20T12:00:00Z",
        }
        repo.upsert_translation_output(base)
        conn.commit()

        updated = dict(base)
        updated.update(
            {
                "display_title": "更新後標題",
                "summary_short": "更新後中文摘要內容。",
                "bullet_1": "更新後第一要點。",
                "source_fingerprint": "fp_2",
                "retry_count": 1,
            }
        )
        repo.upsert_translation_output(updated)
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM translation_output "
            "WHERE parent_content_id = 1 AND language_code = 'zh'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

        row = support.snapshot_translation_row(
            conn, parent_content_id=1, language_code="zh"
        )
        self.assertEqual(row["display_title"], "更新後標題")
        self.assertEqual(row["summary_short"], "更新後中文摘要內容。")
        self.assertEqual(row["bullet_1"], "更新後第一要點。")
        self.assertEqual(row["bullet_2"], "原始第二要點。")
        self.assertEqual(row["source_fingerprint"], "fp_2")
        self.assertEqual(row["retry_count"], 1)


# ---------------------------------------------------------------------------
# TranslationRepository.detect_and_mark_stale (plan Phase 3 item 5)
# ---------------------------------------------------------------------------

class TestDetectAndMarkStale(_TranslateSchemaBase):
    """Stale-marking matrix: fingerprint mismatch, config change on completed
    rows, the bypass exemption, and idempotency on already-stale rows."""

    RUNNING_MODEL = "gpt-5.4-mini"
    RUNNING_PROMPT_VERSION = "translator_v2"

    def _detect(self, conn: sqlite3.Connection) -> List[Tuple[int, str, str]]:
        marked = TranslationRepository(conn).detect_and_mark_stale(
            running_model=self.RUNNING_MODEL,
            running_prompt_version=self.RUNNING_PROMPT_VERSION,
        )
        conn.commit()
        return marked

    def test_fingerprint_mismatch_marks_row_stale(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1, fingerprint="fp_new")
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1,
                status="completed",
                retry_count=0,
                source_fingerprint="fp_outdated",
            ),
        )
        marked = self._detect(conn)
        self.assertEqual(marked, [(1, "zh", "fingerprint_mismatch")])
        row = support.snapshot_translation_row(
            conn, parent_content_id=1, language_code="zh"
        )
        self.assertEqual(row["translation_status"], "stale")

    def test_completed_row_with_config_mismatch_marks_stale(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1)
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1,
                status="completed",
                retry_count=0,
                model_name="superseded-model",
                prompt_version="translator_v1",
            ),
        )
        marked = self._detect(conn)
        self.assertEqual(marked, [(1, "zh", "config_change")])
        row = support.snapshot_translation_row(
            conn, parent_content_id=1, language_code="zh"
        )
        self.assertEqual(row["translation_status"], "stale")

    def test_bypass_rows_are_exempt_from_config_check(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1)
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1,
                status="completed",
                retry_count=0,
                model_name="bypass",
                prompt_version="bypass",
            ),
        )
        marked = self._detect(conn)
        self.assertEqual(marked, [])
        row = support.snapshot_translation_row(
            conn, parent_content_id=1, language_code="zh"
        )
        self.assertEqual(row["translation_status"], "completed")

    def test_bypass_rows_still_marked_stale_on_fingerprint_mismatch(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1, fingerprint="fp_new")
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1,
                status="completed",
                retry_count=0,
                source_fingerprint="fp_outdated",
                model_name="bypass",
                prompt_version="bypass",
            ),
        )
        marked = self._detect(conn)
        self.assertEqual(marked, [(1, "zh", "fingerprint_mismatch")])
        row = support.snapshot_translation_row(
            conn, parent_content_id=1, language_code="zh"
        )
        self.assertEqual(row["translation_status"], "stale")

    def test_already_stale_rows_are_not_rereported(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1, fingerprint="fp_new")
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1,
                status="completed",
                retry_count=0,
                source_fingerprint="fp_outdated",
            ),
        )
        first = self._detect(conn)
        self.assertEqual(len(first), 1)
        second = self._detect(conn)
        self.assertEqual(second, [])
        row = support.snapshot_translation_row(
            conn, parent_content_id=1, language_code="zh"
        )
        self.assertEqual(row["translation_status"], "stale")

    def test_non_completed_rows_are_not_config_staled(self) -> None:
        # The config-mismatch query only scans completed rows.
        conn = self.open_connection()
        seeded_statuses = ((1, "failed", 1), (2, "pending", 0))
        for pid, status, retry_count in seeded_statuses:
            self.seed_record(conn, parent_content_id=pid)
            support.seed_translation_row(
                conn,
                **_translation_kwargs(
                    parent_content_id=pid,
                    status=status,
                    retry_count=retry_count,
                    model_name="superseded-model",
                    prompt_version="translator_v1",
                ),
            )
        marked = self._detect(conn)
        self.assertEqual(marked, [])
        for pid, status, _ in seeded_statuses:
            with self.subTest(status=status):
                row = support.snapshot_translation_row(
                    conn, parent_content_id=pid, language_code="zh"
                )
                self.assertEqual(row["translation_status"], status)


# ---------------------------------------------------------------------------
# TranslationRepository.get_pending_translation_tasks (plan Phase 3 item 5)
# ---------------------------------------------------------------------------

class TestGetPendingTranslationTasks(_TranslateSchemaBase):
    """Queue eligibility matrix: new / pending / stale / retryable failed are
    eligible; locked failed and completed are excluded; multi-language
    selection only returns unfinished languages."""

    RETRY_ATTEMPTS = 3

    def _tasks(
        self, conn: sqlite3.Connection, target_languages: List[str]
    ) -> List[Dict[str, Any]]:
        return TranslationRepository(conn).get_pending_translation_tasks(
            target_languages, retry_attempts=self.RETRY_ATTEMPTS
        )

    def test_status_eligibility_matrix(self) -> None:
        conn = self.open_connection()
        for pid in range(1, 7):
            self.seed_record(conn, parent_content_id=pid)
        # pid 1: no translation row -> eligible as "new".
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=2, status="pending", retry_count=0
            ),
        )
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=3, status="stale", retry_count=0
            ),
        )
        # Retryable failed: retry_count just below the limit.
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=4,
                status="failed",
                retry_count=self.RETRY_ATTEMPTS - 1,
            ),
        )
        # Logically locked failed: retry_count at the limit -> excluded.
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=5,
                status="failed",
                retry_count=self.RETRY_ATTEMPTS,
            ),
        )
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=6, status="completed", retry_count=0
            ),
        )

        tasks = self._tasks(conn, ["zh"])
        by_pid = {task["parent_content_id"]: task for task in tasks}
        self.assertEqual(set(by_pid), {1, 2, 3, 4})
        self.assertEqual(by_pid[1]["status"], "new")
        self.assertEqual(by_pid[1]["retry_count"], 0)
        self.assertEqual(by_pid[2]["status"], "pending")
        self.assertEqual(by_pid[3]["status"], "stale")
        self.assertEqual(by_pid[4]["status"], "failed")
        # The retryable failed task carries its existing retry_count so the
        # runner can keep incrementing towards the limit.
        self.assertEqual(by_pid[4]["retry_count"], self.RETRY_ATTEMPTS - 1)
        for task in tasks:
            self.assertEqual(task["language_code"], "zh")

    def test_multi_language_returns_only_missing_language_task(self) -> None:
        conn = self.open_connection()
        self.seed_record(conn, parent_content_id=1)
        support.seed_translation_row(
            conn,
            **_translation_kwargs(
                parent_content_id=1, status="completed", retry_count=0
            ),
        )
        tasks = self._tasks(conn, ["zh", "ja"])
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["parent_content_id"], 1)
        self.assertEqual(task["language_code"], "ja")
        self.assertEqual(task["status"], "new")
        self.assertEqual(task["retry_count"], 0)

    def test_new_task_carries_approved_record_fields(self) -> None:
        conn = self.open_connection()
        support.seed_approved_record(
            conn,
            parent_content_id=1,
            source_item_id=101,
            display_title="Original English Title",
            summary_short="Original English summary.",
            bullet_1="Claim one.",
            bullet_2="Evidence two.",
            bullet_3=None,
            content_fingerprint="fp_original",
            content_language_code="en",
            approved_at="2026-06-21T08:30:00Z",
        )
        tasks = self._tasks(conn, ["zh"])
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["parent_content_id"], 1)
        self.assertEqual(task["source_item_id"], 101)
        self.assertEqual(task["display_title"], "Original English Title")
        self.assertEqual(task["summary_short"], "Original English summary.")
        self.assertEqual(task["bullet_1"], "Claim one.")
        self.assertEqual(task["bullet_2"], "Evidence two.")
        self.assertIsNone(task["bullet_3"])
        self.assertEqual(task["content_fingerprint"], "fp_original")
        self.assertEqual(task["content_language_code"], "en")
        self.assertEqual(task["approved_at"], "2026-06-21T08:30:00Z")
        self.assertEqual(task["language_code"], "zh")
        self.assertEqual(task["status"], "new")
        self.assertEqual(task["retry_count"], 0)


if __name__ == "__main__":
    unittest.main()
