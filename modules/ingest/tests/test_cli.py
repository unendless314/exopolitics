import unittest
import tempfile
import pathlib
import json
import sys
from io import StringIO
from unittest.mock import patch

from modules.ingest.src.cli import main

class TestCommandLineInterface(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name) / "config"
        self.config_dir.mkdir()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_cli.db"

        # Create valid dummy configurations
        categories_yaml = """
schema_version: 1
categories:
  1:
    name: Enabled Category
    slug: enabled-cat
    enabled: true
"""
        sources_yaml = """
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
    description: Daily cadence
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    html_url: https://example.com
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
"""
        retention_yaml = """
schema_version: 1
raw_retention:
  default_days: 14
  delete_batch_size: 500
  dry_run: false
  audit_log: true
"""
        with open(self.config_dir / "categories.yaml", "w", encoding="utf-8") as f:
            f.write(categories_yaml)
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(sources_yaml)
        with open(self.config_dir / "retention_policy.yaml", "w", encoding="utf-8") as f:
            f.write(retention_yaml)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_validate_success(self) -> None:
        # Capture stdout/stderr
        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            exit_code = main(["--config-dir", str(self.config_dir), "validate"])
        
        self.assertEqual(exit_code, 0)
        self.assertIn("Configuration validated successfully", stdout.getvalue())

    def test_cli_validate_failure(self) -> None:
        # Write bad sources configuration
        bad_sources_yaml = """
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    category_id: 999  # Missing reference
    fetch_group: 3
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
"""
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)

        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            exit_code = main(["--config-dir", str(self.config_dir), "validate"])
        
        self.assertEqual(exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", stderr.getvalue())

    def test_cli_migrate(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            exit_code = main(["migrate", "--db-path", str(self.db_path)])
        
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.db_path.exists())
        self.assertIn("Database schema migrations executed successfully", stdout.getvalue())

    def test_cli_fetch_dry_run(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            exit_code = main(["--config-dir", str(self.config_dir), "fetch", "--db-path", str(self.db_path), "--dry-run"])
        
        self.assertEqual(exit_code, 0)
        self.assertIn("INGEST FETCH RUN COMPLETED", stdout.getvalue())
        self.assertIn("Due Source Count:      1", stdout_capture := stdout.getvalue())



    def test_cli_invalid_arguments_raises(self) -> None:
        with self.assertRaises(SystemExit):
            with patch("sys.stderr", StringIO()):
                main(["invalid_command"])

if __name__ == "__main__":
    unittest.main()
