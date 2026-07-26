import pathlib
import tempfile
import unittest

from modules.ingest.src.database import (
    FetchAttemptRepository,
    FetchRunRepository,
    SourceStateRepository,
    get_connection,
    run_migrations,
)
from modules.ingest.src.errors import ErrorClass, ErrorClassContractError


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


if __name__ == "__main__":
    unittest.main()
