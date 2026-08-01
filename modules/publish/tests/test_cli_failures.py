"""
CLI failure-surface tests (plan section 3.7, EXECUTION_POLICY.md section 7.1
and DATA_CONTRACT.md section 9.2).

The ``validate`` command must fail with a nonzero exit code for a missing
database, a missing ``translation_output`` table, or a configured target
language without any completed translation. The ``run`` and ``rebuild``
commands must treat the same missing-language condition as a warning:
strict-match blocks the affected items, but the command exits successfully
and never reports it as a structural config failure.

Every case uses a temporary config, temporary database and temporary export
directory; the workspace canonical DB and .env are never touched.
"""

import pathlib
import tempfile
import unittest

from click.testing import CliRunner

from modules.publish.src.cli import cli
from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.tests import support

CONFIG_ZH_EN = """
target_languages:
  zh: "Traditional Chinese"
  en: "English"
coverage_policy: "strict_match"
execution_policy:
  default_export_dir: "data/publish_export"
  batch_size: 10
index_policy:
  latest_limit: 5
  archive_granularity: "month"
"""

CONFIG_ZH_EN_JA = """
target_languages:
  zh: "Traditional Chinese"
  en: "English"
  ja: "Japanese"
coverage_policy: "strict_match"
execution_policy:
  default_export_dir: "data/publish_export"
  batch_size: 10
index_policy:
  latest_limit: 5
  archive_granularity: "month"
"""


class TestCliFailureSurface(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.temp_dir.name)
        self.db_path = base / "canonical.db"
        self.export_dir = base / "publish_export"
        self.config_path = base / "settings.yaml"
        self.runner = CliRunner()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_config(self, content: str = CONFIG_ZH_EN) -> None:
        self.config_path.write_text(content, encoding="utf-8")

    def invoke(self, *args: str):
        return self.runner.invoke(cli, ["--config-path", str(self.config_path), *args])

    def test_validate_missing_database_fails(self) -> None:
        self.write_config()
        missing_db = pathlib.Path(self.temp_dir.name) / "does_not_exist.db"
        result = self.invoke("validate", "--db-path", str(missing_db))
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("Database file does not exist", result.output)

    def test_validate_missing_translation_table_fails(self) -> None:
        self.write_config()
        # Create an empty database file without any tables.
        conn = get_connection(self.db_path)
        conn.close()
        result = self.invoke("validate", "--db-path", str(self.db_path))
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("translation_output table does not exist", result.output)

    def test_validate_language_without_completed_translations_fails(self) -> None:
        self.write_config()
        support.create_upstream_tables(self.db_path)
        result = self.invoke("validate", "--db-path", str(self.db_path))
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("has zero completed translations", result.output)

    def test_run_warns_but_succeeds_with_missing_language(self) -> None:
        self.write_config(CONFIG_ZH_EN_JA)
        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)
        support.seed_item(self.db_path, 1, "Two Language Item", "2026-06-15T12:00:00Z")

        with self.assertLogs("publish.orchestrator", level="WARNING") as log:
            result = self.invoke("run", "--db-path", str(self.db_path), "--export-dir", str(self.export_dir))

        self.assertEqual(0, result.exit_code, result.output)
        self.assertTrue(
            any("Target language 'ja' has zero completed translations" in line for line in log.output),
            log.output,
        )
        # Warning-level only: not reported as a structural config failure.
        self.assertNotIn("Configuration validation failed", result.output)
        self.assertIn("Published/Updated: 0", result.output)

        # strict_match blocked the item: no publish state, no public artifact.
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNone(repo.get_publish_record_by_source_item_id(1))
        conn.close()
        self.assertFalse((self.export_dir / "zh" / "items" / "en-two-language-item.json").exists())

    def test_rebuild_warns_but_succeeds_with_missing_language(self) -> None:
        self.write_config(CONFIG_ZH_EN_JA)
        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)
        support.seed_item(self.db_path, 1, "Two Language Item", "2026-06-15T12:00:00Z")

        with self.assertLogs("publish.orchestrator", level="WARNING") as log:
            result = self.invoke("rebuild", "--db-path", str(self.db_path), "--export-dir", str(self.export_dir))

        self.assertEqual(0, result.exit_code, result.output)
        self.assertTrue(
            any("Target language 'ja' has zero completed translations" in line for line in log.output),
            log.output,
        )
        self.assertNotIn("Configuration validation failed", result.output)
        self.assertIn("Published/Updated: 0", result.output)

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNone(repo.get_publish_record_by_source_item_id(1))
        conn.close()

    def test_run_invalid_config_fails_structurally(self) -> None:
        self.config_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
        support.create_upstream_tables(self.db_path)
        result = self.invoke("run", "--db-path", str(self.db_path), "--export-dir", str(self.export_dir))
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("Configuration validation failed", result.output)


if __name__ == "__main__":
    unittest.main()
