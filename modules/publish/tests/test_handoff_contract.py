"""
Real-migration handoff contract test (plan section 7.5 and Phase 4,
DATA_CONTRACT.md section 4).

Unlike ``tests/support.py`` (a publish-owned minimal schema for unit and
integration tests), this suite applies the ACTIVE upstream migrations in
dataflow order — ingest, curate, translate, then publish — into a temporary
database, and verifies:

- every documented publish read dependency exists in the real handoff
  schema with the expected column names,
- the publish tables' required foreign keys are in place, and
- a minimal publishable item seeded through the real schema (including its
  CHECK constraints) passes the minimum publish path.

This test must not import other modules' private test helpers, and it is
not a full pipeline integration suite.
"""

import pathlib
import tempfile
import unittest

from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.tests import support

WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent
INGEST_MIGRATIONS = WORKSPACE_ROOT / "modules" / "ingest" / "src" / "migrations"
CURATE_MIGRATIONS = WORKSPACE_ROOT / "modules" / "curate" / "src" / "migrations"
TRANSLATE_MIGRATIONS = WORKSPACE_ROOT / "modules" / "translate" / "src" / "migrations"

# DATA_CONTRACT.md section 4: the only upstream contracts publish depends on.
DOCUMENTED_READ_DEPENDENCIES = {
    "source_item": {"source_item_id", "canonical_url", "published_at"},
    "approved_content_record": {
        "parent_content_id",
        "source_item_id",
        "content_fingerprint",
        "approved_at",
        "author_metadata",
    },
    "translation_output": {
        "parent_content_id",
        "source_item_id",
        "language_code",
        "display_title",
        "summary_short",
        "bullet_1",
        "bullet_2",
        "bullet_3",
        "source_fingerprint",
        "translation_status",
        "translated_at",
    },
    "curation_decision": {"source_item_id", "curate_status", "downstream_action"},
}


class TestRealMigrationHandoff(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.temp_dir.name)
        self.db_path = base / "canonical.db"
        self.export_dir = base / "publish_export"

        # Apply active migrations in dataflow order.
        for migrations_dir in (
            INGEST_MIGRATIONS,
            CURATE_MIGRATIONS,
            TRANSLATE_MIGRATIONS,
            support.PUBLISH_MIGRATIONS_DIR,
        ):
            self.assertTrue(migrations_dir.exists(), f"missing migrations dir: {migrations_dir}")
            run_migrations(self.db_path, migrations_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def table_columns(self, table: str) -> set:
        conn = get_connection(self.db_path)
        try:
            cursor = conn.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

    def test_documented_read_dependencies_exist_in_real_schema(self) -> None:
        for table, expected_columns in DOCUMENTED_READ_DEPENDENCIES.items():
            with self.subTest(table=table):
                actual = self.table_columns(table)
                missing = expected_columns - actual
                self.assertEqual(set(), missing, f"{table} misses documented columns: {missing}")

    def test_publish_foreign_keys_reference_real_tables(self) -> None:
        conn = get_connection(self.db_path)
        try:
            fk_list = conn.execute("PRAGMA foreign_key_list(publish_record)").fetchall()
            self.assertTrue(
                any(fk[2] == "source_item" and fk[6].upper() == "CASCADE" for fk in fk_list),
                f"publish_record must reference source_item ON DELETE CASCADE: {fk_list}",
            )
            fk_list = conn.execute("PRAGMA foreign_key_list(publish_language_status)").fetchall()
            self.assertTrue(
                any(fk[2] == "publish_record" and fk[6].upper() == "CASCADE" for fk in fk_list),
                f"publish_language_status must reference publish_record ON DELETE CASCADE: {fk_list}",
            )
        finally:
            conn.close()

    def seed_publishable_item_through_real_schema(self) -> None:
        """Insert one fully publishable item, respecting the real CHECK constraints."""
        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                INSERT INTO source_item (
                    source_item_id, source_id, source_item_guid, canonical_url, title,
                    published_at, fetched_at, ingest_dedup_key, dedup_rule, ingest_status
                ) VALUES (1, 1, 'guid-1', 'https://example.com/1', 'Handoff Item',
                          '2026-06-15T12:00:00Z', '2026-06-20T10:00:00Z', 'key_1', 'guid', 'ingested')
            """)
            conn.execute("""
                INSERT INTO curation_decision (
                    source_item_id, curate_status, downstream_action, decision_reason,
                    decision_actor, model_name, prompt_version, curated_at, created_at, updated_at
                ) VALUES (1, 'approved', 'publish_summary', 'Approved', 'operator',
                          'curator', 'v1', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z')
            """)
            conn.execute("""
                INSERT INTO approved_content_record (
                    parent_content_id, source_item_id, display_title, summary_short,
                    bullet_1, bullet_2, bullet_3,
                    content_fingerprint, content_language_code, approved_at,
                    author_metadata, created_at, updated_at
                ) VALUES (10, 1, 'Handoff Item', 'zh 摘要',
                          '主張', '證據', '影響',
                          'fp_123', 'zh', '2026-06-20T12:00:00Z',
                          '{"source_module": "edit", "writer_type": "human", "editor": "john_doe"}',
                          '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z')
            """)
            for translation_id, lang, title in ((100, 'zh', 'Handoff Item'), (101, 'en', 'EN Handoff Item')):
                conn.execute("""
                    INSERT INTO translation_output (
                        translation_output_id, parent_content_id, source_item_id, language_code,
                        display_title, summary_short, bullet_1, bullet_2, bullet_3,
                        source_fingerprint, translation_status, model_name, prompt_version,
                        translated_at, updated_at
                    ) VALUES (?, 10, 1, ?, ?, ?, ?, ?, ?, 'fp_123', 'completed', 'translator', 'v1',
                              '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z')
                """, (
                    translation_id,
                    lang,
                    title,
                    f"{lang} summary",
                    f"{lang} key claim",
                    f"{lang} evidence level",
                    f"{lang} objective impact",
                ))
            conn.commit()
        finally:
            conn.close()

    def test_minimal_publish_path_against_real_schema(self) -> None:
        self.seed_publishable_item_through_real_schema()
        config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

        summary = support.run_publish(config, self.db_path, self.export_dir)
        self.assertEqual(2, summary["published_count"])

        item = support.read_item(self.export_dir, "en", "en-handoff-item")
        self.assertEqual("EN Handoff Item", item["display_title"])
        self.assertEqual("https://example.com/1", item["canonical_url"])
        self.assertEqual("2026-06-15T12:00:00Z", item["source_published_at"])
        self.assertEqual("en key claim", item["bullets"]["key_claim"])

    def test_source_item_delete_cascades_publish_state(self) -> None:
        self.seed_publishable_item_through_real_schema()
        config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)
        support.run_publish(config, self.db_path, self.export_dir)

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNotNone(repo.get_publish_record_by_source_item_id(1))
        conn.execute("DELETE FROM source_item WHERE source_item_id = 1")
        conn.commit()
        self.assertIsNone(repo.get_publish_record_by_source_item_id(1))
        count = conn.execute("SELECT COUNT(*) FROM publish_language_status").fetchone()[0]
        self.assertEqual(0, count)
        conn.close()


if __name__ == "__main__":
    unittest.main()
