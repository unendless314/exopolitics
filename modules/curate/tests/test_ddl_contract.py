import sqlite3
import unittest

from modules.curate.src.database import (
    CurationRepository,
    get_connection,
    run_migrations,
)
from modules.curate.tests.support import (
    CURATE_MIGRATIONS_DIR,
    create_mock_upstream_tables,
    make_temp_workspace,
    seed_upstream_item,
)

CURATE_TABLES = ("curation_decision", "editor_brief", "curation_output")


def _insert_decision(conn, item_id, status, action):
    """Raw-SQL insert into curation_decision, bypassing the repository so the
    DDL constraint layer is exercised directly."""
    conn.execute(
        """
        INSERT INTO curation_decision (
            source_item_id, curate_status, downstream_action, decision_reason,
            decision_actor, retry_count, model_name, prompt_version,
            curated_at, created_at, updated_at
        ) VALUES (?, ?, ?, 'test reason', 'system', 0, 'model', 'v1', 't', 't', 't')
        """,
        (item_id, status, action),
    )


class TestDdlContract(unittest.TestCase):
    """Curate-owned DDL contract, verified against a real temporary SQLite DB
    built by the active v001 migration (never a mocked IntegrityError)."""

    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        self.db_path = self.workspace / "data" / "canonical.db"
        create_mock_upstream_tables(self.db_path)
        run_migrations(self.db_path, CURATE_MIGRATIONS_DIR)
        self.conn = get_connection(self.db_path)
        self.addCleanup(self.conn.close)
        seed_upstream_item(
            self.conn, 1, title="DDL item", text="body", topic_class="core"
        )

    def tearDown(self) -> None:
        self.conn.rollback()

    # --- downstream_action nullability (double-defense regression) ---

    def test_non_failed_status_with_null_action_rejected_by_ddl(self):
        # SQLite CHECK expressions pass on NULL results; the strengthened
        # CHECK must explicitly reject NULL actions for non-failed statuses.
        for status in ("approved", "rejected", "withdrawn"):
            with self.subTest(status=status):
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        _insert_decision(self.conn, 1, status, None)
                finally:
                    self.conn.rollback()

    def test_repository_rejects_null_action_for_non_failed_status(self):
        repo = CurationRepository(self.conn)
        for status in ("approved", "rejected", "withdrawn"):
            with self.subTest(status=status):
                try:
                    with self.assertRaises(ValueError):
                        repo.upsert_curation_decision({
                            "source_item_id": 1,
                            "curate_status": status,
                            "downstream_action": None,
                            "model_name": "model",
                            "prompt_version": "v1",
                        })
                finally:
                    self.conn.rollback()

    def test_failed_status_with_null_action_allowed(self):
        _insert_decision(self.conn, 1, "failed", None)
        self.conn.commit()
        row = self.conn.execute(
            "SELECT curate_status, downstream_action FROM curation_decision WHERE source_item_id = 1"
        ).fetchone()
        self.assertEqual(row["curate_status"], "failed")
        self.assertIsNone(row["downstream_action"])

    # --- UNIQUE semantics ---

    def test_unique_source_item_id_per_table(self):
        _insert_decision(self.conn, 1, "failed", None)
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_decision(self.conn, 1, "failed", None)
        self.conn.rollback()

        repo = CurationRepository(self.conn)
        repo.upsert_editor_brief({
            "source_item_id": 1,
            "brief_goal": "goal",
            "target_format": "link_card",
            "risk_flags": [],
            "tone_guidance": "neutral",
        })
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO editor_brief (source_item_id, brief_goal, target_format, tone_guidance, created_at, updated_at) "
                "VALUES (1, 'other', 'link_card', 'neutral', 't', 't')"
            )
        self.conn.rollback()

        repo.upsert_curation_output({
            "source_item_id": 1,
            "display_title": "title",
            "summary_short": "summary",
        })
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO curation_output (source_item_id, display_title, summary_short, created_at, updated_at) "
                "VALUES (1, 'other', 'other', 't', 't')"
            )
        self.conn.rollback()

    # --- Foreign keys ---

    def test_orphan_rows_rejected(self):
        # No upstream row exists for source_item_id 999.
        with self.assertRaises(sqlite3.IntegrityError):
            _insert_decision(self.conn, 999, "failed", None)
        self.conn.rollback()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO editor_brief (source_item_id, brief_goal, target_format, tone_guidance, created_at, updated_at) "
                "VALUES (999, 'g', 'link_card', 'neutral', 't', 't')"
            )
        self.conn.rollback()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO curation_output (source_item_id, display_title, summary_short, created_at, updated_at) "
                "VALUES (999, 't', 's', 't', 't')"
            )
        self.conn.rollback()

    def test_foreign_key_delete_action_is_cascade(self):
        for table in CURATE_TABLES:
            with self.subTest(table=table):
                fk_rows = self.conn.execute(
                    f"PRAGMA foreign_key_list({table})"
                ).fetchall()
                self.assertEqual(len(fk_rows), 1)
                fk = fk_rows[0]
                self.assertEqual(fk["table"], "source_item")
                self.assertEqual(fk["from"], "source_item_id")
                self.assertEqual(fk["to"], "source_item_id")
                self.assertEqual(fk["on_delete"].upper(), "CASCADE")

    # --- Indexes (lock columns and semantics, not index names) ---

    def _index_column_sets(self, table):
        columns = []
        for idx in self.conn.execute(f"PRAGMA index_list({table})").fetchall():
            cols = [
                info["name"]
                for info in self.conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
            ]
            columns.append(cols)
        return columns

    def test_source_item_id_is_indexed_in_all_tables(self):
        for table in CURATE_TABLES:
            with self.subTest(table=table):
                self.assertIn(["source_item_id"], self._index_column_sets(table))

    def test_status_action_index_exists_on_decision(self):
        self.assertIn(
            ["curate_status", "downstream_action"],
            self._index_column_sets("curation_decision"),
        )


if __name__ == "__main__":
    unittest.main()
