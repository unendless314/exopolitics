"""CLI contract tests: exit codes and failure surfaces for validate,
migrate, and run.

Every test uses temporary configs and databases; the workspace canonical DB
and real .env secrets are never touched (the CLI's dotenv load does not
override variables set by the tests).
"""

import contextlib
import io
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from modules.classify.src.cli import main
from modules.classify.src.database import run_migrations
from modules.classify.tests.helpers import (
    CLASSIFY_MIGRATIONS_DIR,
    create_mock_ingest_tables,
    make_completion_response,
    make_http_response,
    seed_source_item,
    valid_llm_response,
)

MISSING_KEY_ENV = "CLASSIFY_TEST_MISSING_API_KEY"

GOOD_SETTINGS_YAML = """
active_provider: test-provider
active_prompt_template: test_template
request_defaults:
  temperature: 0.1
  top_p: 0.95
  max_output_tokens: 1024
execution_policy:
  batch_size: 10
  max_concurrent_requests: 3
  rate_limit_per_minute: 600
  request_timeout_seconds: 10.0
  retry_attempts: 2
  backoff_factor: 0.01
providers:
  test-provider:
    api_type: openai_compatible
    api_base: https://api.test.com/v1
    api_key_env: TEST_API_KEY
    model_name: test-model
    supports_structured_output: false
"""

GOOD_TEMPLATES_YAML = """
templates:
  test_template:
    version: v4.0
    system_instruction: You are a classifier.
    user_prompt_template: "Title: {title}, Text: {sanitized_text}"
"""


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()
        (self.config_dir / "model_settings.yaml").write_text(GOOD_SETTINGS_YAML, encoding="utf-8")
        (self.config_dir / "prompt_templates.yaml").write_text(GOOD_TEMPLATES_YAML, encoding="utf-8")
        self.db_path = self.root / "canonical.db"
        create_mock_ingest_tables(self.db_path)
        run_migrations(self.db_path, CLASSIFY_MIGRATIONS_DIR)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_bad_migrations_dir(self) -> pathlib.Path:
        bad_dir = self.root / "bad_migrations"
        bad_dir.mkdir()
        (bad_dir / "v999_bad.sql").write_text("THIS IS NOT VALID SQL;", encoding="utf-8")
        return bad_dir

    def result_count(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM classification_result").fetchone()[0]
        finally:
            conn.close()


class TestValidateCommand(CliTestCase):
    def test_validate_success_exit_zero(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["--config-dir", str(self.config_dir), "validate"])
        self.assertEqual(exit_code, 0)
        self.assertIn("validated successfully", stdout.getvalue())

    def test_validate_config_error_exit_one(self) -> None:
        (self.config_dir / "model_settings.yaml").write_text(
            GOOD_SETTINGS_YAML.replace("temperature: 0.1", "temperature: 2.5"), encoding="utf-8"
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(["--config-dir", str(self.config_dir), "validate"])
        self.assertEqual(exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", stderr.getvalue())


class TestMigrateCommand(CliTestCase):
    def test_migrate_success_exit_zero(self) -> None:
        fresh_db = self.root / "fresh.db"
        exit_code = main(["--config-dir", str(self.config_dir), "migrate", "--db-path", str(fresh_db)])
        self.assertEqual(exit_code, 0)
        conn = sqlite3.connect(str(fresh_db))
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'classification_result'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_migrate_error_exit_one(self) -> None:
        bad_dir = self.write_bad_migrations_dir()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "--config-dir", str(self.config_dir),
                "--migrations-dir", str(bad_dir),
                "migrate", "--db-path", str(self.root / "other.db"),
            ])
        self.assertEqual(exit_code, 1)
        self.assertIn("Migration failed", stderr.getvalue())


class TestRunCommand(CliTestCase):
    def test_run_preview_prompts_exit_zero(self) -> None:
        seed_source_item(self.db_path, 1, "Preview Case", "Body text.")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main([
                "--config-dir", str(self.config_dir),
                "run", "--db-path", str(self.db_path), "--preview-prompts",
            ])
        self.assertEqual(exit_code, 0)
        self.assertIn("PREVIEW", stdout.getvalue())

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_run_dry_run_not_committed(self, mock_post) -> None:
        seed_source_item(self.db_path, 1, "Dry Run Case", "Body text.")
        mock_post.return_value = make_completion_response(valid_llm_response())
        exit_code = main([
            "--config-dir", str(self.config_dir),
            "run", "--db-path", str(self.db_path), "--dry-run",
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.result_count(), 0)

    @patch.dict(os.environ, {"TEST_API_KEY": "dummy_key"})
    @patch("httpx.AsyncClient.post")
    def test_run_all_items_failed_exit_one(self, mock_post) -> None:
        seed_source_item(self.db_path, 1, "Failing Case", "Body text.")
        # A non-retryable 400 keeps this failure-path test fast and deterministic.
        mock_post.return_value = make_http_response(400, {"error": {"message": "bad request"}})
        exit_code = main([
            "--config-dir", str(self.config_dir),
            "run", "--db-path", str(self.db_path),
        ])
        self.assertEqual(exit_code, 1)
        self.assertEqual(self.result_count(), 0)

    def test_run_config_error_exit_one(self) -> None:
        (self.config_dir / "model_settings.yaml").write_text(
            GOOD_SETTINGS_YAML.replace("temperature: 0.1", "temperature: 9.9"), encoding="utf-8"
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "--config-dir", str(self.config_dir),
                "run", "--db-path", str(self.db_path),
            ])
        self.assertEqual(exit_code, 1)
        self.assertIn("Configuration validation failed", stderr.getvalue())

    def test_run_migration_error_exit_one(self) -> None:
        bad_dir = self.write_bad_migrations_dir()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "--config-dir", str(self.config_dir),
                "--migrations-dir", str(bad_dir),
                "run", "--db-path", str(self.root / "unmigrated.db"),
            ])
        self.assertEqual(exit_code, 1)
        self.assertIn("Auto-migration failed", stderr.getvalue())

    @patch.dict(os.environ, {MISSING_KEY_ENV: ""})
    def test_run_missing_api_key_exit_one(self) -> None:
        (self.config_dir / "model_settings.yaml").write_text(
            GOOD_SETTINGS_YAML.replace("api_key_env: TEST_API_KEY", f"api_key_env: {MISSING_KEY_ENV}"),
            encoding="utf-8",
        )
        seed_source_item(self.db_path, 1, "No Key Case", "Body text.")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "--config-dir", str(self.config_dir),
                "run", "--db-path", str(self.db_path),
            ])
        self.assertEqual(exit_code, 1)
        self.assertIn("Orchestrator critical failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
