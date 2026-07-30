import asyncio
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from modules.ingest.src.config import validate_and_load_config
from modules.ingest.src.database import (
    FetchAttemptRepository,
    FetchRunRepository,
    SourceStateRepository,
    get_connection,
    run_migrations,
)
from modules.ingest.src.errors import ErrorClass, ErrorClassContractError
from modules.ingest.src.fetcher import FetchResult
from modules.ingest.src.orchestrator import orchestrate_run
from modules.ingest.tests import feed_samples, integration_helpers


class TestErrorClassContractRepositories(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "test.db"
        migrations_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "migrations"
        run_migrations(self.db_path, migrations_dir)
        self.conn = get_connection(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_error_class_enum_matches_application_contract(self) -> None:
        self.assertEqual(
            {error_class.value for error_class in ErrorClass},
            {
                "network_error",
                "timeout_error",
                "http_error_4xx",
                "http_error_5xx",
                "parse_error",
                "unexpected_error",
            },
        )

    def test_source_state_rejects_invalid_error_class_and_accepts_null(self) -> None:
        repository = SourceStateRepository(self.conn)

        with self.assertRaisesRegex(
            ErrorClassContractError,
            r"source_state\.last_error_class",
        ):
            repository.upsert(101, {"last_error_class": "out_of_contract"})

        repository.upsert(101, {"last_error_class": None})
        self.conn.commit()

        state = repository.get(101)
        self.assertIsNone(state["last_error_class"])

    def test_fetch_attempt_rejects_invalid_error_class_and_accepts_null(self) -> None:
        run_id = FetchRunRepository(self.conn).create(
            run_scope="test",
            trigger_type="manual",
            due_source_count=1,
        )
        repository = FetchAttemptRepository(self.conn)
        invalid_attempt = {
            "fetch_run_id": run_id,
            "source_id": 101,
            "started_at": "2026-07-26T00:00:00Z",
            "outcome": "failed",
            "error_class": "out_of_contract",
        }

        with self.assertRaisesRegex(
            ErrorClassContractError,
            r"fetch_attempt\.error_class",
        ):
            repository.insert(invalid_attempt)

        repository.insert({**invalid_attempt, "error_class": None})
        self.conn.commit()

        row = self.conn.execute(
            "SELECT error_class FROM fetch_attempt WHERE fetch_run_id = ?",
            (run_id,),
        ).fetchone()
        self.assertIsNone(row["error_class"])


class TestErrorContractIntegration(unittest.TestCase):
    """Pipeline-level error-class contracts: emittable classes persist to
    state and attempt, out-of-contract classes fail the run without a
    persistence fallback, and the fallback path annotates or withholds fetch
    error detail as specified. Moved from the former test_integration.py
    during the Phase 6 test-structure split; test logic and assertions are
    unchanged.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = pathlib.Path(self.temp_dir.name)
        self.db_path = self.config_dir / "test.db"

        integration_helpers.write_base_config(self.config_dir)
        integration_helpers.write_sources(
            self.config_dir, integration_helpers.source_block(title="Test Feed Source")
        )

        self.migrations_dir = integration_helpers.MIGRATIONS_DIR

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_each_emittable_error_class_persists_to_state_and_attempt(self, mock_fetch) -> None:
        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        cases = (
            (ErrorClass.NETWORK, None),
            (ErrorClass.TIMEOUT, None),
            (ErrorClass.HTTP_4XX, 404),
            (ErrorClass.HTTP_5XX, 503),
            (ErrorClass.PARSE, None),
            (ErrorClass.UNEXPECTED, None),
        )

        for error_class, status_code in cases:
            with self.subTest(error_class=error_class.value):
                mock_fetch.return_value = FetchResult(
                    status_code=status_code,
                    content=None,
                    etag=None,
                    last_modified=None,
                    error_class=error_class.value,
                    error_detail="mock failure",
                    retry_count=0,
                )

                summary = asyncio.run(orchestrate_run(
                    config=config,
                    db_path=self.db_path,
                    trigger_type="manual",
                    force=True,
                ))

                self.assertEqual(summary.run_status, "partial_failure")
                conn = get_connection(self.db_path)
                try:
                    state = conn.execute(
                        "SELECT last_error_class FROM source_state WHERE source_id = 101"
                    ).fetchone()
                    attempt = conn.execute(
                        "SELECT error_class FROM fetch_attempt WHERE fetch_run_id = ? AND source_id = 101",
                        (summary.fetch_run_id,),
                    ).fetchone()
                    self.assertEqual(state["last_error_class"], error_class.value)
                    self.assertEqual(attempt["error_class"], error_class.value)
                finally:
                    conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_invalid_error_class_fails_run_without_persistence_fallback(self, mock_fetch) -> None:
        with open(self.config_dir / "sources.yaml", "w", encoding="utf-8") as f:
            f.write("""
schema_version: 1
schedule_classes:
  daily:
    target_interval_minutes: 1440
    description: Daily
sanitization_profiles:
  default_html_article:
    input_preference: [summary]
    decode_entities: true
    remove_selectors: [script]
sources:
  - id: 101
    title: Invalid Error Class Source
    xml_url: https://example.com/invalid
    category_id: 1
    fetch_group: 1
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
  - id: 102
    title: Healthy Source
    xml_url: https://example.com/healthy
    category_id: 1
    fetch_group: 1
    schedule_class: daily
    sanitization_profile: default_html_article
    enabled: true
""")

        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        def fake_fetch(xml_url, **kwargs):
            if xml_url.endswith("/invalid"):
                return FetchResult(
                    status_code=599,
                    content=None,
                    etag=None,
                    last_modified=None,
                    error_class="out_of_contract",
                    error_detail="invalid error class",
                    retry_count=0,
                )
            return FetchResult(
                status_code=200,
                content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
                etag=None,
                last_modified=None,
                error_class=None,
                error_detail=None,
                retry_count=0,
            )

        mock_fetch.side_effect = fake_fetch
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=self.db_path,
            trigger_type="manual",
            force=True,
        ))

        self.assertEqual(summary.run_status, "failed")
        self.assertEqual(summary.attempted_source_count, 2)
        self.assertEqual(summary.succeeded_source_count, 1)
        self.assertEqual(summary.failed_source_count, 1)
        self.assertIn("Source 101 OrchestrationException", summary.error_summary)
        self.assertIn("source_state.last_error_class", summary.error_summary)
        self.assertIn("out_of_contract", summary.error_summary)

        conn = get_connection(self.db_path)
        try:
            healthy_state = conn.execute(
                "SELECT * FROM source_state WHERE source_id = 102"
            ).fetchone()
            healthy_attempt = conn.execute(
                "SELECT outcome FROM fetch_attempt WHERE fetch_run_id = ? AND source_id = 102",
                (summary.fetch_run_id,),
            ).fetchone()
            invalid_state = conn.execute(
                "SELECT * FROM source_state WHERE source_id = 101"
            ).fetchone()
            invalid_attempt = conn.execute(
                "SELECT * FROM fetch_attempt WHERE fetch_run_id = ? AND source_id = 101",
                (summary.fetch_run_id,),
            ).fetchone()

            self.assertIsNotNone(healthy_state)
            self.assertEqual(healthy_attempt["outcome"], "success")
            self.assertIsNone(invalid_state)
            self.assertIsNone(invalid_attempt)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_invalid_error_class_restores_existing_source_state(self, mock_fetch) -> None:
        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
            etag="original-etag",
            last_modified=None,
            error_class=None,
            error_detail=None,
            retry_count=0,
        )
        first_summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=self.db_path,
            trigger_type="manual",
            force=True,
        ))
        self.assertEqual(first_summary.run_status, "success")

        conn = get_connection(self.db_path)
        try:
            prior_state = dict(conn.execute(
                "SELECT * FROM source_state WHERE source_id = 101"
            ).fetchone())
        finally:
            conn.close()

        mock_fetch.return_value = FetchResult(
            status_code=599,
            content=None,
            etag=None,
            last_modified=None,
            error_class="out_of_contract",
            error_detail="invalid error class",
            retry_count=0,
        )
        summary = asyncio.run(orchestrate_run(
            config=config,
            db_path=self.db_path,
            trigger_type="manual",
            force=True,
        ))

        self.assertEqual(summary.run_status, "failed")
        conn = get_connection(self.db_path)
        try:
            current_state = dict(conn.execute(
                "SELECT * FROM source_state WHERE source_id = 101"
            ).fetchone())
            attempt_count = conn.execute(
                "SELECT COUNT(*) FROM fetch_attempt WHERE fetch_run_id = ? AND source_id = 101",
                (summary.fetch_run_id,),
            ).fetchone()[0]
            self.assertEqual(current_state, prior_state)
            self.assertEqual(attempt_count, 0)
            self.assertEqual(current_state["consecutive_failures"], 0)
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_persistence_fallback_preserves_fetch_class_and_status_without_detail(self, mock_fetch) -> None:
        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        remote_error_detail = "remote response excerpt must not persist"
        mock_fetch.return_value = FetchResult(
            status_code=429,
            content=None,
            etag=None,
            last_modified=None,
            error_class=ErrorClass.HTTP_4XX.value,
            error_detail=remote_error_detail,
            retry_count=1,
        )
        original_upsert = SourceStateRepository.upsert

        def fail_initial_failure_state_update(repository, source_id, state_data):
            if state_data["last_error_class"] == ErrorClass.HTTP_4XX.value:
                raise sqlite3.OperationalError("mock persistence failure")
            return original_upsert(repository, source_id, state_data)

        with patch.object(
            SourceStateRepository,
            "upsert",
            fail_initial_failure_state_update,
        ):
            summary = asyncio.run(orchestrate_run(
                config=config,
                db_path=self.db_path,
                trigger_type="manual",
                force=True,
            ))

        self.assertEqual(summary.run_status, "partial_failure")
        conn = get_connection(self.db_path)
        try:
            attempt = conn.execute(
                "SELECT http_status, error_class, error_detail FROM fetch_attempt "
                "WHERE fetch_run_id = ? AND source_id = 101",
                (summary.fetch_run_id,),
            ).fetchone()
            self.assertIsNone(attempt["http_status"])
            self.assertEqual(attempt["error_class"], ErrorClass.UNEXPECTED.value)
            self.assertIn("fetch_error_class=http_error_4xx", attempt["error_detail"])
            self.assertIn("fetch_http_status=429", attempt["error_detail"])
            self.assertNotIn(remote_error_detail, attempt["error_detail"])
        finally:
            conn.close()

    @patch("modules.ingest.src.orchestrator.fetch_feed")
    def test_success_flow_persistence_fallback_has_no_fetch_error_annotation(self, mock_fetch) -> None:
        run_migrations(self.db_path, self.migrations_dir)
        config, errors, warnings = validate_and_load_config(self.config_dir)
        self.assertEqual(len(errors), 0)

        mock_fetch.return_value = FetchResult(
            status_code=200,
            content=feed_samples.RSS_TWO_ARTICLES.encode("utf-8"),
            etag=None,
            last_modified=None,
            error_class=None,
            error_detail=None,
            retry_count=0,
        )
        original_upsert = SourceStateRepository.upsert

        def fail_success_state_update(repository, source_id, state_data):
            if state_data["last_error_class"] is None:
                raise sqlite3.OperationalError("mock persistence failure")
            return original_upsert(repository, source_id, state_data)

        with patch.object(
            SourceStateRepository,
            "upsert",
            fail_success_state_update,
        ):
            summary = asyncio.run(orchestrate_run(
                config=config,
                db_path=self.db_path,
                trigger_type="manual",
                force=True,
            ))

        self.assertEqual(summary.run_status, "partial_failure")
        conn = get_connection(self.db_path)
        try:
            attempt = conn.execute(
                "SELECT error_detail FROM fetch_attempt WHERE fetch_run_id = ? AND source_id = 101",
                (summary.fetch_run_id,),
            ).fetchone()
            self.assertNotIn("fetch_error_class=", attempt["error_detail"])
            self.assertNotIn("fetch_http_status=", attempt["error_detail"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
