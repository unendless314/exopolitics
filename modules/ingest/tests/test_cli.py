import unittest
import tempfile
import pathlib
import json
import sys
from io import StringIO
from modules.ingest.src.cli import main

class TestCommandLineInterface(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name) / "config"
        self.config_dir.mkdir()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_cli.db"

        # Create valid dummy configurations
        categories_yaml = """
schema_version: 2
categories:
  1:
    name: Enabled Category
    slug: enabled-cat
    enabled: true
"""
        sources_yaml = """
schema_version: 2
schedule_classes:
  daily:
    target_interval_minutes: 1440
    description: Daily cadence
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    html_url: https://example.com
    category_id: 1
    fetch_group: 3
    schedule_class: daily
    enabled: true
"""
        with open(self.config_dir / "categories.yaml", "w", encoding="utf-8") as f:
            f.write(categories_yaml)
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(sources_yaml)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_validate_success(self) -> None:
        # Redirect stdout to capture output
        stdout_capture = StringIO()
        with patch_stdout(stdout_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "validate"])
        
        self.assertEqual(exit_code, 0)
        self.assertIn("Configuration validated successfully", stdout_capture.getvalue())

    def test_cli_validate_failure(self) -> None:
        # Overwrite sources.yaml with bad category reference to trigger failure
        bad_sources_yaml = """
schema_version: 2
schedule_classes:
  daily:
    target_interval_minutes: 1440
    description: Daily cadence
sources:
  - id: 101
    title: Test Feed
    xml_url: https://example.com/rss
    category_id: 999  # Does not exist
    fetch_group: 3
    schedule_class: daily
    enabled: true
"""
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write(bad_sources_yaml)

        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "validate"])
        
        self.assertEqual(exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", stderr_capture.getvalue())

    def test_cli_migrate(self) -> None:
        stdout_capture = StringIO()
        with patch_stdout(stdout_capture):
            exit_code = main(["migrate", "--db-path", str(self.db_path)])
        
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.db_path.exists())
        self.assertIn("Database schema migrations executed successfully", stdout_capture.getvalue())

    def test_cli_fetch_dry_run(self) -> None:
        stdout_capture = StringIO()
        with patch_stdout(stdout_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "fetch", "--db-path", str(self.db_path), "--dry-run"])
        
        self.assertEqual(exit_code, 0)
        self.assertIn("INGEST FETCH RUN COMPLETED", stdout_capture.getvalue())
        self.assertIn("Due Source Count:      1", stdout_capture.getvalue())

    def test_cli_fetch_dry_run_json(self) -> None:
        stdout_capture = StringIO()
        with patch_stdout(stdout_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "fetch", "--db-path", str(self.db_path), "--dry-run", "--json"])
        
        self.assertEqual(exit_code, 0)
        
        # Verify it's a valid JSON string
        summary = json.loads(stdout_capture.getvalue())
        self.assertEqual(summary["due_source_count"], 1)
        self.assertEqual(summary["run_status"], "success")

    def test_cli_show_health(self) -> None:
        stdout_capture = StringIO()
        with patch_stdout(stdout_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "show-health", "--db-path", str(self.db_path)])
        
        self.assertEqual(exit_code, 0)
        self.assertIn("ID     | Title", stdout_capture.getvalue())
        self.assertIn("101    | Test Feed", stdout_capture.getvalue())

    def test_cli_show_health_json(self) -> None:
        stdout_capture = StringIO()
        with patch_stdout(stdout_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "show-health", "--db-path", str(self.db_path), "--json"])
        
        self.assertEqual(exit_code, 0)
        report = json.loads(stdout_capture.getvalue())
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["source_id"], 101)
        self.assertEqual(report[0]["health_status"], "healthy")

    def test_cli_invalid_arguments_raises(self) -> None:
        # Argparse raises SystemExit on invalid arguments
        with self.assertRaises(SystemExit):
            # Redirect stderr to avoid cluttering test output
            with patch_stderr(StringIO()):
                main(["invalid_command"])

# Helpers to redirect stdout and stderr
import contextlib

@contextlib.contextmanager
def patch_stdout(stream):
    old_stdout = sys.stdout
    sys.stdout = stream
    try:
        yield
    finally:
        sys.stdout = old_stdout

@contextlib.contextmanager
def patch_stderr(stream):
    old_stderr = sys.stderr
    sys.stderr = stream
    try:
        yield
    finally:
        sys.stderr = old_stderr

if __name__ == "__main__":
    unittest.main()
