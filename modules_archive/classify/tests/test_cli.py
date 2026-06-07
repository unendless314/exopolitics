import contextlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch, AsyncMock
import httpx

from modules.classify.src.cli import load_local_env, main
from modules.classify.src.repository import run_migrations

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

class TestClassifyCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name) / "config"
        self.config_dir.mkdir()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test_cli.db"

        # Apply ingest migrations first to ensure source_item table exists
        self.ingest_migrations_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "ingest" / "src" / "migrations"
        run_migrations(self.db_path, self.ingest_migrations_dir)

        # Create valid configurations
        self.model_settings_yaml = """
active_provider: openai
active_prompt_template: single_item_v2

request_defaults:
  temperature: 0.1
  top_p: 0.95
  max_output_tokens: 1024

execution_policy:
  batch_size: 20
  max_concurrent_requests: 3
  rate_limit_per_minute: 60
  request_timeout_seconds: 5.0
  min_context_characters: 100
  retry_attempts: 1
  backoff_factor: 0.001

providers:
  openai:
    api_type: openai
    api_key_env: TEST_OPENAI_API_KEY
    model_name: gpt-5.4-mini
    supports_structured_output: true

deterministic_classification:
  model_name: deterministic-low-context
  prompt_version: rule_v1
"""
        self.prompt_templates_yaml = """
templates:
  single_item_v2:
    version: v2.0
    description: Test prompt
    system_instruction: "Instruction"
    user_prompt_template: "Title: {title} Summary: {summary}"
"""
        with open(self.config_dir / "model_settings.yaml", "w", encoding="utf-8") as f:
            f.write(self.model_settings_yaml)
        with open(self.config_dir / "prompt_templates.yaml", "w", encoding="utf-8") as f:
            f.write(self.prompt_templates_yaml)

        # Mock environmental variable
        self.env_patcher = patch.dict("os.environ", {"TEST_OPENAI_API_KEY": "test-cli-key"})
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.env_patcher.stop()

    def test_cli_migrate(self) -> None:
        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--db-path", str(self.db_path), "migrate"])
        
        self.assertEqual(exit_code, 0)
        self.assertTrue(self.db_path.exists())
        self.assertIn("Migrations applied successfully", stderr_capture.getvalue())

    def test_cli_run_no_pending(self) -> None:
        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "--db-path", str(self.db_path), "run"])
        
        self.assertEqual(exit_code, 0)
        self.assertIn("No pending items found to classify", stderr_capture.getvalue())

    @patch("httpx.AsyncClient.post")
    def test_cli_run_with_pending(self, mock_post) -> None:
        # Migrate DB first
        main(["--db-path", str(self.db_path), "migrate"])

        # Insert unclassified items
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        # 1. Low context item
        conn.execute("""
            INSERT INTO source_item (source_id, title, summary, fetched_at, ingest_dedup_key, dedup_rule, ingest_status, created_at)
            VALUES (1, 'Thin', 'Short', '2026-06-05T00:00:00Z', 'k1', 'guid', 'ingested', '2026-06-05T00:00:00Z')
        """)
        # 2. Standard item
        conn.execute("""
            INSERT INTO source_item (source_id, title, summary, fetched_at, ingest_dedup_key, dedup_rule, ingest_status, created_at)
            VALUES (1, 'Encounter', 'A military aviation sighting of a cigar-shaped UAP at high speeds over the Pacific Ocean. This event was witnessed by multiple radar systems and aircraft crew.', '2026-06-05T00:00:00Z', 'k2', 'guid', 'ingested', '2026-06-05T00:00:00Z')
        """)
        conn.commit()
        conn.close()

        # Mock LLM API response for standard item
        mock_response = httpx.Response(200, json={
            "choices": [{
                "message": {
                    "content": '{"topic_class": "core", "classification_confidence": 0.95, "edit_candidate": 1, "classification_reason": "Encounter text"}'
                }
            }]
        })
        mock_post.return_value = mock_response

        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "--db-path", str(self.db_path), "run"])

        self.assertEqual(exit_code, 0, f"CLI command failed with stderr: {stderr_capture.getvalue()}")
        output = stderr_capture.getvalue()
        
        # Verify both low-context check and standard execution completed
        self.assertIn("Found 2 pending items to classify", output)
        self.assertIn("Item 0001: [LOW-CONTEXT] -> classified as unknown", output)
        self.assertIn("Item 0002: [LLM] -> classified as core", output)
        self.assertIn("CLASSIFICATION RUN COMPLETED", output)
        self.assertIn("LLM Successes:         1", output)
        self.assertIn("Low-Context Skipped:   1", output)

    def test_cli_export_report_no_db(self) -> None:
        missing_db = pathlib.Path(self.temp_dir.name) / "missing_to_report.db"
        report_path = pathlib.Path(self.temp_dir.name) / "no_db.html"
        
        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--db-path", str(missing_db), "export-report", "--out", str(report_path)])
            
        self.assertEqual(exit_code, 1)
        self.assertIn("Database file does not exist", stderr_capture.getvalue())

    def test_cli_export_report_success(self) -> None:
        # Migrate DB and insert source item and classification result
        main(["--db-path", str(self.db_path), "migrate"])
        
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            INSERT INTO source_item (source_item_id, source_id, title, summary, fetched_at, ingest_dedup_key, dedup_rule, ingest_status, created_at)
            VALUES (5, 1, 'My Title', 'My Summary', '2026-06-05T00:00:00Z', 'key_val', 'guid', 'ingested', '2026-06-05T00:00:00Z')
        """)
        conn.execute("""
            INSERT INTO classification_result (source_item_id, topic_class, classification_reason, classification_confidence, edit_candidate, model_name, prompt_version, classified_at, created_at)
            VALUES (5, 'core', 'Direct sight', 0.99, 1, 'gpt-5.4-mini', 'v2.0', '2026-06-05T00:01:00Z', '2026-06-05T00:01:00Z')
        """)
        conn.commit()
        conn.close()

        report_path = pathlib.Path(self.temp_dir.name) / "report.html"
        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--db-path", str(self.db_path), "export-report", "--out", str(report_path)])

        self.assertEqual(exit_code, 0)
        self.assertTrue(report_path.exists())
        html = report_path.read_text(encoding="utf-8")
        self.assertIn("My Title", html)
        self.assertIn("My Summary", html)
        self.assertIn("Direct sight", html)
        self.assertIn("gpt-5.4-mini", html)
        self.assertIn("badge-core", html)
        self.assertIn("99.00%", html)

    def test_cli_fail_fast_on_fresh_db(self) -> None:
        fresh_db = pathlib.Path(self.temp_dir.name) / "fresh_no_ingest.db"
        stderr_capture = StringIO()
        
        # Test migrate fails fast
        with patch_stderr(stderr_capture):
            exit_code = main(["--db-path", str(fresh_db), "migrate"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Required upstream table 'source_item' is missing", stderr_capture.getvalue())

        # Test run fails fast
        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--config-dir", str(self.config_dir), "--db-path", str(fresh_db), "run"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Required upstream table 'source_item' is missing", stderr_capture.getvalue())

    def test_cli_export_report_escaping(self) -> None:
        main(["--db-path", str(self.db_path), "migrate"])
        
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        # Insert raw tags in columns
        conn.execute("""
            INSERT INTO source_item (source_item_id, source_id, title, summary, fetched_at, ingest_dedup_key, dedup_rule, ingest_status, created_at)
            VALUES (6, 1, 'My <Script> Title', 'My <Danger> Summary', '2026-06-05T00:00:00Z', 'key_val_esc', 'guid', 'ingested', '2026-06-05T00:00:00Z')
        """)
        conn.execute("""
            INSERT INTO classification_result (source_item_id, topic_class, classification_reason, classification_confidence, edit_candidate, model_name, prompt_version, classified_at, created_at)
            VALUES (6, 'core', '<Alert> Direct sight', 0.99, 1, 'gpt-5.4-mini', 'v2.0', '2026-06-05T00:01:00Z', '2026-06-05T00:01:00Z')
        """)
        conn.commit()
        conn.close()

        report_path = pathlib.Path(self.temp_dir.name) / "esc_report.html"
        stderr_capture = StringIO()
        with patch_stderr(stderr_capture):
            exit_code = main(["--db-path", str(self.db_path), "export-report", "--out", str(report_path)])

        self.assertEqual(exit_code, 0)
        self.assertTrue(report_path.exists())
        html_out = report_path.read_text(encoding="utf-8")
        
        # Check that characters are escaped
        self.assertIn("My &lt;Script&gt; Title", html_out)
        self.assertIn("My &lt;Danger&gt; Summary", html_out)
        self.assertIn("&lt;Alert&gt; Direct sight", html_out)
        
        # Verify no raw tag injection
        self.assertNotIn("<Script>", html_out)
        self.assertNotIn("<Danger>", html_out)
        self.assertNotIn("<Alert>", html_out)

    def test_load_local_env_reads_workspace_dotenv_without_override(self) -> None:
        env_path = pathlib.Path(self.temp_dir.name) / ".env"
        env_path.write_text("TEST_OPENAI_API_KEY=from-dotenv\n", encoding="utf-8")

        with patch.dict(os.environ, {}, clear=True):
            load_local_env(pathlib.Path(self.temp_dir.name))
            self.assertEqual(os.environ.get("TEST_OPENAI_API_KEY"), "from-dotenv")

        with patch.dict(os.environ, {"TEST_OPENAI_API_KEY": "existing-value"}, clear=True):
            load_local_env(pathlib.Path(self.temp_dir.name))
            self.assertEqual(os.environ.get("TEST_OPENAI_API_KEY"), "existing-value")

if __name__ == "__main__":
    unittest.main()
