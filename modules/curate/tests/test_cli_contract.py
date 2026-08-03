import os
import pathlib
import unittest
from unittest.mock import AsyncMock, patch

import yaml
from click.testing import CliRunner

from modules.curate.src.cli import cli
from modules.curate.src.config import validate_and_load_config
from modules.curate.src.database import get_connection, run_migrations
from modules.curate.tests.support import (
    CURATE_MIGRATIONS_DIR,
    create_mock_upstream_tables,
    make_chat_completion_payload,
    make_mock_http_response,
    make_temp_workspace,
    make_valid_response,
    seed_curation_state,
    seed_upstream_item,
    snapshot_curate_tables,
)

VALID_SETTINGS = {
    "active_provider": "test-provider",
    "active_prompt_template": "test_template",
    "request_defaults": {"temperature": 0.2, "top_p": 0.95, "max_output_tokens": 512},
    "execution_policy": {
        "batch_size": 20,
        "max_concurrent_requests": 3,
        "rate_limit_per_minute": 6000,
        "request_timeout_seconds": 10.0,
        "retry_attempts": 2,
        "backoff_factor": 0.1,
    },
    "providers": {
        "test-provider": {
            "api_type": "openai_compatible",
            "api_key_env": "TEST_CURATE_API_KEY",
            "model_name": "test-model",
            "supports_structured_output": False,
            "api_base": "https://api.test.local",
        },
    },
}

VALID_TEMPLATES = {
    "templates": {
        "test_template": {
            "version": "test-v1",
            "description": "contract test template",
            "system_instruction": "SYS INSTRUCTION MARKER",
            "user_prompt_template": (
                "Title: {raw_title}\nText: {sanitized_text}\n"
                "Topic: {topic_class}\nGov: {governmental_involvement}"
            ),
        },
    },
}


def _parse_status_counts(output: str) -> dict:
    """Parses the user-visible status summary into labeled counts."""
    counts = {}
    section = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("pending:"):
            counts["pending"] = int(line.split(":")[1])
        elif line.startswith("locked"):
            counts["locked"] = int(line.rsplit(":")[1])
        elif line.startswith("approved:"):
            counts["approved"] = int(line.split(":")[1])
            section = "approved"
        elif line.startswith("withdrawn:"):
            counts["withdrawn"] = int(line.split(":")[1])
            section = "withdrawn"
        elif line.startswith("rejected:"):
            counts["rejected"] = int(line.split(":")[1])
            section = "rejected"
        elif line.startswith("- publish_link:"):
            counts[f"{section}_publish_link"] = int(line.split(":")[1])
        elif line.startswith("- publish_summary:"):
            counts[f"{section}_publish_summary"] = int(line.split(":")[1])
        elif line.startswith("- edit_rewrite:"):
            counts[f"{section}_edit_rewrite"] = int(line.split(":")[1])
        elif line.startswith("- reject_discard:"):
            counts[f"{section}_reject_discard"] = int(line.split(":")[1])
        elif line.startswith("total_failed_runs:"):
            counts["total_failed_runs"] = int(line.split(":")[1])
    return counts


class TestCliContract(unittest.TestCase):
    """User-visible CLI command contract.

    Every test runs on a temporary config, a temporary workspace database at
    `<workspace>/data/canonical.db` (so the runner lock stays inside the
    workspace), mocked HTTP, and a patched no-op `load_dotenv` so no `.env`
    secret or workspace canonical DB is ever touched.
    """

    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        self.db_path = self.workspace / "data" / "canonical.db"
        self.config_dir = self.workspace / "config"
        self.config_dir.mkdir(parents=True)
        self._write_config(self.config_dir, VALID_SETTINGS, VALID_TEMPLATES)
        self.runner = CliRunner()

        dotenv_patch = patch("modules.curate.src.cli.load_dotenv", lambda *a, **k: None)
        dotenv_patch.start()
        self.addCleanup(dotenv_patch.stop)

        env_patch = patch.dict(os.environ, {"TEST_CURATE_API_KEY": "test-api-key"})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    # --- helpers ---

    def _write_config(self, config_dir, settings, templates):
        (config_dir / "model_settings.yaml").write_text(
            yaml.safe_dump(settings), encoding="utf-8"
        )
        (config_dir / "prompt_templates.yaml").write_text(
            yaml.safe_dump(templates), encoding="utf-8"
        )

    def _invoke(self, args, *, config_dir=None, migrations_dir=None):
        full_args = [
            "--config-dir", str(config_dir or self.config_dir),
            "--migrations-dir", str(migrations_dir or CURATE_MIGRATIONS_DIR),
            *args,
        ]
        return self.runner.invoke(cli, full_args)

    def _prepare_db(self):
        create_mock_upstream_tables(self.db_path)
        run_migrations(self.db_path, CURATE_MIGRATIONS_DIR)

    def _seed_item(self, item_id, *, title, topic_class="core"):
        conn = get_connection(self.db_path)
        try:
            seed_upstream_item(conn, item_id, title=title, text="body", topic_class=topic_class)
        finally:
            conn.close()

    # --- validate ---

    def test_validate_success(self):
        result = self._invoke(["validate"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Configuration validated successfully", result.output)

    def test_validate_missing_config_files(self):
        empty_dir = self.workspace / "empty_config"
        empty_dir.mkdir()
        result = self._invoke(["validate"], config_dir=empty_dir)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", result.output)

    def test_validate_invalid_yaml(self):
        (self.config_dir / "model_settings.yaml").write_text("[unclosed", encoding="utf-8")
        result = self._invoke(["validate"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", result.output)

    def test_validate_unknown_active_provider(self):
        bad_settings = dict(VALID_SETTINGS, active_provider="ghost")
        self._write_config(self.config_dir, bad_settings, VALID_TEMPLATES)
        result = self._invoke(["validate"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", result.output)

    def test_validate_unknown_active_template(self):
        bad_settings = dict(VALID_SETTINGS, active_prompt_template="ghost")
        self._write_config(self.config_dir, bad_settings, VALID_TEMPLATES)
        result = self._invoke(["validate"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", result.output)

    def test_validate_invalid_execution_policy(self):
        bad_settings = dict(VALID_SETTINGS)
        bad_settings["execution_policy"] = dict(VALID_SETTINGS["execution_policy"], batch_size=0)
        self._write_config(self.config_dir, bad_settings, VALID_TEMPLATES)
        result = self._invoke(["validate"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("CONFIG VALIDATION FAILED", result.output)

    def test_validate_top_p_null_loads_as_none(self):
        null_top_p_settings = dict(
            VALID_SETTINGS,
            request_defaults={"temperature": 1.0, "top_p": None, "max_output_tokens": 512},
        )
        self._write_config(self.config_dir, null_top_p_settings, VALID_TEMPLATES)
        result = self._invoke(["validate"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Configuration validated successfully", result.output)

        config = validate_and_load_config(self.config_dir)
        self.assertIsNone(config.request_defaults.top_p)

    def test_active_config_pins_top_p_incident_baseline(self):
        active_config_dir = pathlib.Path(__file__).resolve().parent.parent / "config"
        config = validate_and_load_config(active_config_dir)
        self.assertEqual(config.request_defaults.temperature, 1.0)
        self.assertIsNone(config.request_defaults.top_p)

    # --- migrate ---

    def test_migrate_success_and_idempotent_rerun(self):
        result = self._invoke(["migrate", "--db-path", str(self.db_path)])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("migrations executed successfully", result.output)

        conn = get_connection(self.db_path)
        try:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            conn.close()
        self.assertIn("curation_decision", tables)

        rerun = self._invoke(["migrate", "--db-path", str(self.db_path)])
        self.assertEqual(rerun.exit_code, 0, msg=rerun.output)

    def test_migrate_failure_reports_error(self):
        bad_dir = self.workspace / "bad_migrations"
        bad_dir.mkdir()
        (bad_dir / "v999_bad.sql").write_text("THIS IS NOT VALID SQL;", encoding="utf-8")
        result = self._invoke(["migrate", "--db-path", str(self.db_path)], migrations_dir=bad_dir)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Migration failed", result.output)

    # --- status ---

    def test_status_counts_and_zero_side_effects(self):
        self._prepare_db()
        conn = get_connection(self.db_path)
        try:
            seed_upstream_item(conn, 1, title="pending item", text="body", topic_class="core")
            seed_upstream_item(conn, 2, title="retry item", text="body", topic_class="core")
            seed_curation_state(conn, 2, curate_status="failed", downstream_action=None, retry_count=1)
            seed_upstream_item(conn, 3, title="locked item", text="body", topic_class="core")
            seed_curation_state(conn, 3, curate_status="failed", downstream_action=None, retry_count=3)
            seed_upstream_item(conn, 4, title="approved link", text="body", topic_class="core")
            seed_curation_state(conn, 4, curate_status="approved", downstream_action="publish_link")
            seed_upstream_item(conn, 5, title="approved summary", text="body", topic_class="core")
            seed_curation_state(conn, 5, curate_status="approved", downstream_action="publish_summary")
            seed_upstream_item(conn, 6, title="withdrawn link", text="body", topic_class="core")
            seed_curation_state(conn, 6, curate_status="withdrawn", downstream_action="publish_link", decision_actor="operator")
            seed_upstream_item(conn, 7, title="rejected rewrite", text="body", topic_class="core")
            seed_curation_state(conn, 7, curate_status="rejected", downstream_action="edit_rewrite")
            seed_upstream_item(conn, 8, title="rejected discard", text="body", topic_class="core")
            seed_curation_state(conn, 8, curate_status="rejected", downstream_action="reject_discard")
            seed_upstream_item(conn, 9, title="irrelevant item", text="body", topic_class="irrelevant")
            before = snapshot_curate_tables(conn)
        finally:
            conn.close()

        result = self._invoke(["status", "--db-path", str(self.db_path)])
        self.assertEqual(result.exit_code, 0, msg=result.output)

        counts = _parse_status_counts(result.output)
        self.assertEqual(counts["pending"], 2)
        self.assertEqual(counts["locked"], 1)
        self.assertEqual(counts["approved"], 2)
        self.assertEqual(counts["approved_publish_link"], 1)
        self.assertEqual(counts["approved_publish_summary"], 1)
        self.assertEqual(counts["withdrawn"], 1)
        self.assertEqual(counts["withdrawn_publish_link"], 1)
        self.assertEqual(counts["withdrawn_publish_summary"], 0)
        self.assertEqual(counts["rejected"], 2)
        self.assertEqual(counts["rejected_edit_rewrite"], 1)
        self.assertEqual(counts["rejected_reject_discard"], 1)
        self.assertEqual(counts["total_failed_runs"], 2)

        conn = get_connection(self.db_path)
        try:
            after = snapshot_curate_tables(conn)
        finally:
            conn.close()
        self.assertEqual(after, before)

    # --- run --preview-prompts ---

    def test_run_preview_prompts_no_llm_no_writes(self):
        self._prepare_db()
        self._seed_item(1, title="Preview Item One")
        self._seed_item(2, title="Already Approved Item")
        conn = get_connection(self.db_path)
        try:
            seed_curation_state(
                conn, 2, curate_status="approved", downstream_action="publish_summary",
                with_brief=True, with_output=True,
            )
            before = snapshot_curate_tables(conn)
        finally:
            conn.close()

        with patch("httpx.AsyncClient.post") as mock_post:
            result = self._invoke(["run", "--db-path", str(self.db_path), "--preview-prompts"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        mock_post.assert_not_called()
        self.assertIn("PREVIEW PROMPTS MODE", result.output)
        self.assertIn("SYS INSTRUCTION MARKER", result.output)
        self.assertIn("Preview Item One", result.output)
        self.assertNotIn("Already Approved Item", result.output)

        conn = get_connection(self.db_path)
        try:
            self.assertEqual(snapshot_curate_tables(conn), before)
        finally:
            conn.close()

    # --- run --dry-run ---

    def test_run_dry_run_calls_llm_but_writes_nothing(self):
        self._prepare_db()
        self._seed_item(1, title="Dry Run Item")

        calls = {"count": 0}

        async def ok_post(self, *args, **kwargs):
            calls["count"] += 1
            return make_mock_http_response(
                status_code=200,
                json_data=make_chat_completion_payload(make_valid_response("publish_summary")),
            )

        with patch("httpx.AsyncClient.post", ok_post), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = self._invoke(["run", "--db-path", str(self.db_path), "--dry-run"])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertGreaterEqual(calls["count"], 1)
        self.assertIn("Processed Successfully", result.output)

        conn = get_connection(self.db_path)
        try:
            self.assertEqual(
                snapshot_curate_tables(conn),
                {"curation_decision": [], "editor_brief": [], "curation_output": []},
            )
        finally:
            conn.close()

    # --- run --force usage and exit behavior ---

    def test_run_force_without_source_item_id_is_usage_error(self):
        result = self._invoke(["run", "--db-path", str(self.db_path), "--force"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--force can only be used", result.output)

    def test_run_all_items_failed_exits_nonzero(self):
        self._prepare_db()
        self._seed_item(1, title="Always Fails")

        async def fail_post(self, *args, **kwargs):
            return make_mock_http_response(status_code=503, text="server error")

        with patch("httpx.AsyncClient.post", fail_post), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = self._invoke(["run", "--db-path", str(self.db_path)])

        self.assertEqual(result.exit_code, 1, msg=result.output)
        self.assertIn("Failures:", result.output)

    def test_run_partial_failure_exits_zero(self):
        self._prepare_db()
        self._seed_item(1, title="FAIL ITEM title")
        self._seed_item(2, title="OK ITEM title")

        async def routed_post(self, url, headers=None, json=None, timeout=None):
            if "FAIL ITEM" in json["messages"][1]["content"]:
                return make_mock_http_response(status_code=503, text="server error")
            return make_mock_http_response(
                status_code=200,
                json_data=make_chat_completion_payload(make_valid_response("reject_discard")),
            )

        with patch("httpx.AsyncClient.post", routed_post), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = self._invoke(["run", "--db-path", str(self.db_path)])

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Failures:", result.output)


if __name__ == "__main__":
    unittest.main()
