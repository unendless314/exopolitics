"""Click CLI contract tests (TRANSLATE_TEST_MAINTAINABILITY_PLAN Phase 3,
work items 3 and 7; plan sections 3.4, 3.7 and 7 item 5).

Public CLI surface covered here:

- validate: success confirmation and broken-config failure surface.
- run --preview-prompts: prints prompt payloads, never calls the API and
  never writes to the DB.
- run --dry-run: issues the (mocked) LLM requests but persists nothing and
  skips stale detection.
- run summary/exit codes: two-language success (Source Items Selected /
  Language Tasks Queried), missing API key, all-fail (exit 1) and
  partial-fail (exit 0) outcomes.
- run --batch-size: counts source items selected by approved_at ASC,
  parent_content_id ASC, not language tasks.
- run --force: usage error without --parent-content-id; a successful forced
  rerun of a fresh completed row atomically overwrites its content.
- status: queue summary header plus one section per configured language.
- assemble: rejection reporting for illegal upstream bullet shapes.

Isolation contract (plan section 7 item 5): every test patches
modules.translate.src.cli.load_dotenv so the CLI group callback never reads
the workspace .env; configs are written by support.write_config_dir into a
TemporaryDirectory; databases come from support.build_temp_workspace so the
process lock file also stays inside the temp workspace;
TEST_TRANSLATE_API_KEY is set explicitly per invoke; HTTP is always faked
through httpx.AsyncClient.post; asyncio.sleep and random.uniform are patched
on every failure/retry path. No real network, no real waiting, no workspace
canonical DB, no .env reads.
"""

import contextlib
import pathlib
import re
import sqlite3
import tempfile
import unittest
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import patch

from click.testing import CliRunner

from modules.translate.src.cli import cli
from modules.translate.src.database import get_connection
from modules.translate.tests import support


# ---------------------------------------------------------------------------
# Response bodies and language fixtures
# ---------------------------------------------------------------------------

def _valid_zh_body() -> Dict[str, Any]:
    """zh response passing all runner-side validation rules (CJK present)."""
    return support.make_chat_completion_body(
        support.make_five_field_response(
            title="標題",
            summary="這是一段中文翻譯摘要內容。",
            bullet_1="第一要點內容。",
            bullet_2="第二要點內容。",
            bullet_3="第三要點內容。",
        )
    )


def _valid_ja_body() -> Dict[str, Any]:
    """ja response passing all runner-side validation rules (kana present)."""
    return support.make_chat_completion_body(
        support.make_five_field_response(
            title="タイトル",
            summary="これは日本語への翻訳要約です。",
            bullet_1="第一の要点です。",
            bullet_2="第二の要点です。",
            bullet_3="第三の要点です。",
        )
    )


def _zh_only_languages() -> Dict[str, Dict[str, Any]]:
    return {"zh": {"label": "Traditional Chinese", "max_title_length": 120}}


def _zh_ja_languages() -> Dict[str, Dict[str, Any]]:
    return {
        "zh": {"label": "Traditional Chinese", "max_title_length": 120},
        "ja": {"label": "Japanese", "max_title_length": 120},
    }


# ---------------------------------------------------------------------------
# Small assertion / patching helpers
# ---------------------------------------------------------------------------

def _summary_value(output: str, label: str) -> str:
    """Extracts the number from a '  Label:   N' summary line without
    coupling to the exact whitespace padding."""
    match = re.search(re.escape(label) + r":\s+(\d+)", output)
    if match is None:
        raise AssertionError(f"summary line {label!r} not found in output:\n{output}")
    return match.group(1)


def _all_translation_rows(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM translation_output ORDER BY translation_output_id"
        ).fetchall()
    ]


@contextlib.contextmanager
def _no_wait_execution(fake_post: Any) -> Iterator[List[float]]:
    """Patches the HTTP client, asyncio.sleep and the jitter source for one
    CLI invoke: no real network and no real waiting (plan section 3.4).

    Yields the recorded asyncio.sleep durations for tests that want to
    assert on backoff/stagger behavior.
    """
    sleeps: List[float] = []

    async def fake_sleep(duration: float) -> None:
        sleeps.append(duration)

    with patch("httpx.AsyncClient.post", new=fake_post), patch(
        "modules.translate.src.orchestrator.asyncio.sleep", new=fake_sleep
    ), patch(
        "modules.translate.src.orchestrator.random.uniform", return_value=0.0
    ):
        yield sleeps


def _seed_english_record(
    db_path: pathlib.Path,
    *,
    parent_content_id: int,
    source_item_id: int,
    approved_at: str = "2026-06-20T12:00:00Z",
) -> None:
    conn = get_connection(db_path)
    try:
        support.seed_approved_record(
            conn,
            parent_content_id=parent_content_id,
            source_item_id=source_item_id,
            display_title=f"English Title {parent_content_id}",
            summary_short=f"English summary content {parent_content_id}.",
            bullet_1="Claim content.",
            bullet_2="Evidence content.",
            bullet_3="Impact content.",
            content_fingerprint=f"fp_{parent_content_id}",
            content_language_code="en",
            approved_at=approved_at,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Base class: mandatory per-test isolation (plan section 7 item 5)
# ---------------------------------------------------------------------------

class _CliBase(unittest.TestCase):
    """Every CLI test runs inside one TemporaryDirectory: config dir, DB and
    lock file never escape it. load_dotenv is patched on every invoke and the
    API key is set explicitly, so tests never read the workspace .env or rely
    on ambient environment."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.tmp = pathlib.Path(self.temp_dir.name)
        self.runner = CliRunner()

    def write_config(
        self,
        *,
        target_languages: Optional[Dict[str, Dict[str, Any]]] = None,
        **overrides: Any,
    ) -> pathlib.Path:
        kwargs: Dict[str, Any] = {
            # 5.0 is the locked production policy (commit bc165eb).
            "content_ratio_limit": 5.0,
            "supports_structured_output": False,
            "target_languages": (
                target_languages if target_languages is not None else _zh_only_languages()
            ),
        }
        kwargs.update(overrides)
        return support.write_config_dir(self.tmp / "config", **kwargs)

    def build_workspace(self) -> pathlib.Path:
        return support.build_temp_workspace(self.tmp / "workspace")

    def invoke_cli(self, args: List[str], *, api_key: str = "test-key"):
        with patch("modules.translate.src.cli.load_dotenv"), patch.dict(
            "os.environ", {"TEST_TRANSLATE_API_KEY": api_key}
        ):
            return self.runner.invoke(cli, args)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidateCommand(_CliBase):
    """validate: config dir checked through the real CLI group callback."""

    def test_validate_success_prints_confirmation(self) -> None:
        config_dir = self.write_config()
        result = self.invoke_cli(["--config-dir", str(config_dir), "validate"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Configuration validated successfully", result.output)

    def test_validate_broken_config_dir_fails(self) -> None:
        config_dir = self.tmp / "broken_config"
        config_dir.mkdir()
        (config_dir / "model_settings.yaml").write_text(
            "providers: [unclosed\n", encoding="utf-8"
        )
        (config_dir / "prompt_templates.yaml").write_text(
            "templates: {}\n", encoding="utf-8"
        )
        result = self.invoke_cli(["--config-dir", str(config_dir), "validate"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("CONFIG VALIDATION FAILED", result.output)


# ---------------------------------------------------------------------------
# run --preview-prompts
# ---------------------------------------------------------------------------

class TestRunPreviewPrompts(_CliBase):
    """run --preview-prompts: prints prompt payloads without calling the API
    or writing to the DB (Phase 3 acceptance: preview does not call the LLM,
    does not update stale state, does not write rows)."""

    def test_preview_prints_prompts_without_api_calls_or_db_writes(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)

        conn = get_connection(db_path)
        try:
            before_record = support.snapshot_approved_record(conn, source_item_id=100)
            before_translations = _all_translation_rows(conn)
        finally:
            conn.close()

        api_calls: List[Dict[str, Any]] = []

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            api_calls.append({"url": url, "json": json})
            return support.make_http_response(200, _valid_zh_body())

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                [
                    "--config-dir", str(config_dir),
                    "run", "--db-path", str(db_path), "--preview-prompts",
                ]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("PREVIEW TRANSLATION PROMPT", result.output)
        self.assertEqual(_summary_value(result.output, "Source Items Selected"), "1")
        self.assertEqual(_summary_value(result.output, "Previewed Prompts"), "1")
        self.assertEqual(api_calls, [], "preview must never call the LLM API")

        conn = get_connection(db_path)
        try:
            after_record = support.snapshot_approved_record(conn, source_item_id=100)
            after_translations = _all_translation_rows(conn)
        finally:
            conn.close()
        self.assertEqual(before_record, after_record)
        self.assertEqual(before_translations, [])
        self.assertEqual(after_translations, [])


# ---------------------------------------------------------------------------
# run --dry-run
# ---------------------------------------------------------------------------

class TestRunDryRun(_CliBase):
    """run --dry-run: issues the (mocked) LLM requests but leaves no
    persisted translate writes and skips stale detection (Phase 3
    acceptance)."""

    def test_dry_run_calls_api_without_persisting_writes_or_stale_detection(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)
        _seed_english_record(db_path, parent_content_id=2, source_item_id=101)

        # A completed row whose fingerprint no longer matches the source: a
        # normal run would mark it stale and re-translate it. Dry-run skips
        # stale detection, so it must stay completed and untouched.
        conn = get_connection(db_path)
        try:
            support.seed_translation_row(
                conn,
                parent_content_id=2,
                source_item_id=101,
                language_code="zh",
                display_title="既有標題",
                summary_short="既有摘要內容。",
                bullet_1="既有要點一。",
                bullet_2="既有要點二。",
                bullet_3="既有要點三。",
                source_fingerprint="fp_outdated",
                status="completed",
                retry_count=0,
            )
            before_stale_candidate = support.snapshot_translation_row(
                conn, parent_content_id=2, language_code="zh"
            )
        finally:
            conn.close()

        api_calls: List[Dict[str, Any]] = []

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            api_calls.append({"url": url, "json": json})
            return support.make_http_response(200, _valid_zh_body())

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                [
                    "--config-dir", str(config_dir),
                    "run", "--db-path", str(db_path), "--dry-run",
                ]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_summary_value(result.output, "Source Items Selected"), "1")
        self.assertEqual(_summary_value(result.output, "Language Tasks Queried"), "1")
        self.assertEqual(
            len(api_calls),
            1,
            "dry-run must issue the LLM request for the eligible task only",
        )

        conn = get_connection(db_path)
        try:
            new_row = support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="zh"
            )
            after_stale_candidate = support.snapshot_translation_row(
                conn, parent_content_id=2, language_code="zh"
            )
        finally:
            conn.close()
        self.assertIsNone(new_row, "dry-run must not persist translation writes")
        self.assertEqual(
            before_stale_candidate,
            after_stale_candidate,
            "dry-run skips stale detection; the mismatched completed row is untouched",
        )


# ---------------------------------------------------------------------------
# run summary and exit-code surface
# ---------------------------------------------------------------------------

class TestRunSummaryAndExitCodes(_CliBase):
    """run exit/output contract: two-language success counts, missing API
    key, all-fail and partial-fail outcomes (Phase 3 item 3)."""

    def test_two_language_success_summary_counts(self) -> None:
        config_dir = self.write_config(target_languages=_zh_ja_languages())
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)

        api_calls: List[Dict[str, Any]] = []

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            api_calls.append({"url": url, "json": json})
            user_message = json["messages"][1]["content"]
            body = _valid_ja_body() if "(ja)" in user_message else _valid_zh_body()
            return support.make_http_response(200, body)

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                ["--config-dir", str(config_dir), "run", "--db-path", str(db_path)]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_summary_value(result.output, "Source Items Selected"), "1")
        self.assertEqual(_summary_value(result.output, "Language Tasks Queried"), "2")
        self.assertEqual(_summary_value(result.output, "Processed Successfully"), "2")
        self.assertEqual(len(api_calls), 2)

        conn = get_connection(db_path)
        try:
            zh_row = support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="zh"
            )
            ja_row = support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="ja"
            )
        finally:
            conn.close()
        self.assertIsNotNone(zh_row)
        self.assertIsNotNone(ja_row)
        self.assertEqual(zh_row["translation_status"], "completed")
        self.assertEqual(ja_row["translation_status"], "completed")
        self.assertEqual(zh_row["display_title"], "標題")
        self.assertEqual(ja_row["display_title"], "タイトル")

    def test_missing_api_key_exits_1_with_critical_failure(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)

        api_calls: List[Dict[str, Any]] = []

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            api_calls.append({"url": url, "json": json})
            return support.make_http_response(200, _valid_zh_body())

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                ["--config-dir", str(config_dir), "run", "--db-path", str(db_path)],
                api_key="",
            )

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("Orchestrator critical failure", result.output)
        self.assertEqual(
            api_calls, [], "no API request may be issued without an API key"
        )

    def test_all_tasks_fail_exits_1(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)

        api_calls: List[Dict[str, Any]] = []

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            api_calls.append({"url": url, "json": json})
            return support.make_http_response(500, {"error": "simulated server error"})

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                ["--config-dir", str(config_dir), "run", "--db-path", str(db_path)]
            )

        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(_summary_value(result.output, "Processed Successfully"), "0")
        self.assertEqual(_summary_value(result.output, "Failures"), "1")
        # 5xx is retryable: the task consumes the full configured retry budget
        # (write_config_dir default retry_attempts=3) before failing.
        self.assertEqual(len(api_calls), 3)

        conn = get_connection(db_path)
        try:
            row = support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="zh"
            )
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["translation_status"], "failed")
        self.assertEqual(row["retry_count"], 1)

    def test_partial_failure_exits_0_and_reports_one_failure(self) -> None:
        config_dir = self.write_config(target_languages=_zh_ja_languages())
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            user_message = json["messages"][1]["content"]
            if "(ja)" in user_message:
                return support.make_http_response(500, {"error": "simulated server error"})
            return support.make_http_response(200, _valid_zh_body())

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                ["--config-dir", str(config_dir), "run", "--db-path", str(db_path)]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_summary_value(result.output, "Processed Successfully"), "1")
        self.assertEqual(_summary_value(result.output, "Failures"), "1")

        conn = get_connection(db_path)
        try:
            zh_row = support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="zh"
            )
            ja_row = support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="ja"
            )
        finally:
            conn.close()
        self.assertEqual(zh_row["translation_status"], "completed")
        self.assertEqual(ja_row["translation_status"], "failed")


# ---------------------------------------------------------------------------
# run --batch-size
# ---------------------------------------------------------------------------

class TestRunBatchSize(_CliBase):
    """--batch-size counts source items selected by approved_at ASC,
    parent_content_id ASC (plan section 7 item 6), not language tasks."""

    def test_batch_size_1_selects_exactly_one_source_item(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()
        # Equal approved_at: parent_content_id is the deterministic tie-breaker,
        # so parent_content_id=1 must be the selected article.
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)
        _seed_english_record(db_path, parent_content_id=2, source_item_id=101)

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            return support.make_http_response(200, _valid_zh_body())

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                [
                    "--config-dir", str(config_dir),
                    "run", "--db-path", str(db_path), "--batch-size", "1",
                ]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_summary_value(result.output, "Source Items Selected"), "1")
        self.assertEqual(_summary_value(result.output, "Language Tasks Queried"), "1")

        conn = get_connection(db_path)
        try:
            selected_row = support.snapshot_translation_row(
                conn, parent_content_id=1, language_code="zh"
            )
            unselected_row = support.snapshot_translation_row(
                conn, parent_content_id=2, language_code="zh"
            )
        finally:
            conn.close()
        self.assertIsNotNone(selected_row)
        self.assertEqual(selected_row["translation_status"], "completed")
        self.assertIsNone(
            unselected_row,
            "the article beyond the batch boundary must not be translated",
        )

    def test_non_positive_batch_size_is_usage_error(self) -> None:
        # Code-review P1 (2026-08-01): the CLI rejects non-positive overrides
        # at option parsing (click.IntRange), before any config load, DB
        # access or API traffic.
        for bad in ("0", "-2"):
            with self.subTest(batch_size=bad):
                config_dir = self.write_config()
                result = self.invoke_cli(
                    [
                        "--config-dir", str(config_dir),
                        "run", "--batch-size", bad,
                    ]
                )
                self.assertEqual(result.exit_code, 2, result.output)
                self.assertIn("Invalid value", result.output)


# ---------------------------------------------------------------------------
# run --force
# ---------------------------------------------------------------------------

class TestRunForce(_CliBase):
    """--force contract (Phase 3 item 3; plan section 7 item 8)."""

    def test_force_without_parent_content_id_is_usage_error(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()
        result = self.invoke_cli(
            [
                "--config-dir", str(config_dir),
                "run", "--db-path", str(db_path), "--force",
            ]
        )
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--force can only be used", result.output)

    def test_force_on_fresh_completed_record_overwrites_content_on_success(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=5, source_item_id=500)

        # A fresh completed row: source fingerprint, model and prompt version
        # all match the running config, so stale detection leaves it
        # completed and --force takes the non-persisted forced-rerun path.
        conn = get_connection(db_path)
        try:
            support.seed_translation_row(
                conn,
                parent_content_id=5,
                source_item_id=500,
                language_code="zh",
                display_title="舊標題",
                summary_short="舊摘要內容。",
                bullet_1="舊要點一。",
                bullet_2="舊要點二。",
                bullet_3="舊要點三。",
                source_fingerprint="fp_5",
                status="completed",
                retry_count=0,
                model_name="gpt-5.4-mini",
                prompt_version="translator_v2",
            )
            before = support.snapshot_translation_row(
                conn, parent_content_id=5, language_code="zh"
            )
        finally:
            conn.close()

        async def fake_post(self, url, headers=None, json=None, timeout=None):
            return support.make_http_response(200, _valid_zh_body())

        with _no_wait_execution(fake_post):
            result = self.invoke_cli(
                [
                    "--config-dir", str(config_dir),
                    "run", "--db-path", str(db_path),
                    "--parent-content-id", "5", "--force",
                ]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(_summary_value(result.output, "Processed Successfully"), "1")

        conn = get_connection(db_path)
        try:
            after = support.snapshot_translation_row(
                conn, parent_content_id=5, language_code="zh"
            )
        finally:
            conn.close()
        self.assertEqual(before["display_title"], "舊標題")
        self.assertNotEqual(before, after)
        self.assertEqual(after["display_title"], "標題")
        self.assertEqual(after["summary_short"], "這是一段中文翻譯摘要內容。")
        self.assertEqual(after["bullet_1"], "第一要點內容。")
        self.assertEqual(after["translation_status"], "completed")
        self.assertEqual(after["retry_count"], 0)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatusCommand(_CliBase):
    """status: queue summary header plus one section per configured target
    language (Phase 3 item 3)."""

    def test_status_prints_summary_and_per_language_sections(self) -> None:
        config_dir = self.write_config(target_languages=_zh_ja_languages())
        db_path = self.build_workspace()
        _seed_english_record(db_path, parent_content_id=1, source_item_id=100)

        conn = get_connection(db_path)
        try:
            support.seed_translation_row(
                conn,
                parent_content_id=1,
                source_item_id=100,
                language_code="zh",
                display_title="標題",
                summary_short="這是一段中文翻譯摘要內容。",
                bullet_1="第一要點內容。",
                bullet_2="第二要點內容。",
                bullet_3="第三要點內容。",
                source_fingerprint="fp_1",
                status="completed",
                retry_count=0,
            )
            support.seed_translation_row(
                conn,
                parent_content_id=1,
                source_item_id=100,
                language_code="ja",
                display_title=None,
                summary_short=None,
                bullet_1=None,
                bullet_2=None,
                bullet_3=None,
                source_fingerprint="fp_1",
                status="failed",
                retry_count=1,
            )
        finally:
            conn.close()

        result = self.invoke_cli(
            ["--config-dir", str(config_dir), "status", "--db-path", str(db_path)]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("TRANSLATE QUEUE STATUS SUMMARY", result.output)
        self.assertIn("Language: ZH (Traditional Chinese)", result.output)
        self.assertIn("Language: JA (Japanese)", result.output)


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------

class TestAssembleCommand(_CliBase):
    """assemble: zero-trust upstream bullet-shape rejection is reported with
    per-item diagnostics while legal items in the same assembly continue
    (plan section 3.1, section 7 item 7)."""

    def test_assemble_reports_rejected_invalid_upstream_shape(self) -> None:
        config_dir = self.write_config()
        db_path = self.build_workspace()

        conn = get_connection(db_path)
        try:
            support.seed_curation_approval(
                conn,
                source_item_id=1001,
                downstream_action="publish_summary",
                display_title="Valid Approval",
                summary_short="Valid summary content.",
                bullet_1="Claim content.",
                bullet_2="Evidence content.",
                bullet_3="Impact content.",
            )
            # Illegal shape: publish_summary with one NULL bullet.
            support.seed_curation_approval(
                conn,
                source_item_id=1002,
                downstream_action="publish_summary",
                display_title="Illegal Approval",
                summary_short="Illegal summary content.",
                bullet_1="Claim content.",
                bullet_2=None,
                bullet_3="Impact content.",
            )
        finally:
            conn.close()

        result = self.invoke_cli(
            ["--config-dir", str(config_dir), "assemble", "--db-path", str(db_path)]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("HANDOFF ASSEMBLY COMPLETED", result.output)
        self.assertEqual(
            _summary_value(result.output, "Rejected (Invalid Upstream Shape)"), "1"
        )
        self.assertEqual(_summary_value(result.output, "Inserted Records"), "1")
        self.assertIn("source_item_id=1002", result.output)

        conn = get_connection(db_path)
        try:
            self.assertIsNotNone(
                support.snapshot_approved_record(conn, source_item_id=1001)
            )
            self.assertIsNone(
                support.snapshot_approved_record(conn, source_item_id=1002)
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
