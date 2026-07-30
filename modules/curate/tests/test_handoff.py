import sqlite3
import unittest

from modules.curate.src.database import (
    CurationRepository,
    get_connection,
    run_migrations,
)
from modules.curate.tests.support import (
    CLASSIFY_MIGRATIONS_DIR,
    CURATE_MIGRATIONS_DIR,
    INGEST_MIGRATIONS_DIR,
    make_temp_workspace,
    seed_curation_state,
)


def _run_active_migrations(db_path):
    """Applies the three modules' ACTIVE migration scripts in pipeline order.

    This is the only authoritative schema source for the upstream handoff
    contract; no hand-copied schema or other module's test helpers are used.
    """
    for migrations_dir in (INGEST_MIGRATIONS_DIR, CLASSIFY_MIGRATIONS_DIR, CURATE_MIGRATIONS_DIR):
        run_migrations(db_path, migrations_dir)


class TestUpstreamHandoff(unittest.TestCase):
    """Handoff contract between curate and the active ingest/classify schemas.

    The database is built from the three modules' active migration scripts,
    so these tests fail loudly if an upstream schema change breaks curate's
    queue selection or foreign-key assumptions.
    """

    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        self.db_path = self.workspace / "data" / "canonical.db"
        _run_active_migrations(self.db_path)
        self.conn = get_connection(self.db_path)
        self.addCleanup(self.conn.close)

    def tearDown(self) -> None:
        self.conn.rollback()

    def _seed_item(self, item_id, *, topic_class, governmental_involvement=1):
        """Seeds one fully valid upstream item through the active schema."""
        self.conn.execute(
            """
            INSERT INTO source_item (
                source_item_id, source_id, title, fetched_at,
                ingest_dedup_key, dedup_rule, ingest_status
            ) VALUES (?, 1, ?, 't', ?, 'guid', 'ingested')
            """,
            (item_id, f"Handoff Item {item_id}", f"dedup-{item_id}"),
        )
        self.conn.execute(
            """
            INSERT INTO source_item_text (
                source_item_id, sanitized_text, sanitization_method, html_detected,
                was_truncated, text_processing_status, sanitized_text_length,
                created_at, updated_at
            ) VALUES (?, 'handoff body text', 'test', 0, 0, 'completed', 17, 't', 't')
            """,
            (item_id,),
        )
        self.conn.execute(
            """
            INSERT INTO classification_result (
                source_item_id, topic_class, governmental_involvement,
                model_name, prompt_version, classified_at, created_at
            ) VALUES (?, ?, ?, 'm', 'v', 't', 't')
            """,
            (item_id, topic_class, governmental_involvement),
        )
        self.conn.commit()

    def test_pending_selection_against_active_schema(self):
        self._seed_item(1, topic_class="core")       # no decision -> pending
        self._seed_item(2, topic_class="adjacent")   # no decision -> pending
        self._seed_item(3, topic_class="irrelevant") # excluded by topic
        self._seed_item(4, topic_class="unknown")    # excluded by topic
        self._seed_item(5, topic_class="core")
        seed_curation_state(
            self.conn, 5, curate_status="failed", downstream_action=None, retry_count=1
        )  # retry-eligible -> pending
        self._seed_item(6, topic_class="core")
        seed_curation_state(
            self.conn, 6, curate_status="failed", downstream_action=None, retry_count=3
        )  # locked -> not pending
        self._seed_item(7, topic_class="core")
        seed_curation_state(
            self.conn, 7, curate_status="approved", downstream_action="publish_summary"
        )  # completed -> not pending

        repo = CurationRepository(self.conn)
        pending_ids = {row["source_item_id"] for row in repo.get_pending_items(limit=50)}
        self.assertEqual(pending_ids, {1, 2, 5})

    def test_ingest_status_check_allows_only_ingested(self):
        # The active ingest schema forbids non-'ingested' rows. Curate's
        # `ingest_status = 'ingested'` queue filter relies on this upstream
        # guarantee; a past hand-copied fixture allowed 'draft' and hid the
        # drift that this test now locks.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO source_item (source_id, title, fetched_at, ingest_dedup_key, dedup_rule, ingest_status) "
                "VALUES (1, 'draft item', 't', 'draft-key', 'guid', 'draft')"
            )
        self.conn.rollback()

    def test_source_item_delete_restricted_until_text_removed(self):
        self._seed_item(1, topic_class="core")
        seed_curation_state(
            self.conn, 1,
            curate_status="approved", downstream_action="publish_summary",
            with_brief=True, with_output=True,
        )

        # The ingest-owned ON DELETE RESTRICT FK on source_item_text blocks
        # deleting the source item while its text row exists; the blocked
        # delete must leave curate rows untouched.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM source_item WHERE source_item_id = 1")
        self.conn.rollback()
        for table in ("curation_decision", "editor_brief", "curation_output"):
            row = self.conn.execute(
                f"SELECT 1 FROM {table} WHERE source_item_id = 1"
            ).fetchone()
            self.assertIsNotNone(row, f"{table} row must survive the restricted delete")

        # Once the ingest-owned text row is removed first, the delete proceeds
        # and curate-owned ON DELETE CASCADE FKs clean up dependent rows.
        self.conn.execute("DELETE FROM source_item_text WHERE source_item_id = 1")
        self.conn.execute("DELETE FROM source_item WHERE source_item_id = 1")
        self.conn.commit()
        for table in ("curation_decision", "editor_brief", "curation_output"):
            row = self.conn.execute(
                f"SELECT 1 FROM {table} WHERE source_item_id = 1"
            ).fetchone()
            self.assertIsNone(row, f"{table} row must cascade after the allowed delete")


if __name__ == "__main__":
    unittest.main()
