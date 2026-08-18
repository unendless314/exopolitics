"""
Author metadata validation matrix (plan section 3.8 and 7.2,
DATA_CONTRACT.md section 6.1, EXECUTION_POLICY.md section 7.2).

``author_metadata`` must parse to a JSON object with a trim-non-empty string
``source_module`` and an allowlisted ``writer_type``; human/hybrid writer
types additionally require a trim-non-empty string ``editor``. No JSON type
coercion is applied to either field. Every failure case must be rejected
before any public artifact or publish-layer state is produced, and an
invalid update to an already-published item must leave the original DB
state and artifact untouched.
"""

import copy
import pathlib
import tempfile
import unittest
from typing import Any, Dict, List, Tuple

from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.src.orchestrator import (
    validate_item_payload,
    get_disclosure_note,
    ValidationError,
)
from modules.publish.tests import support

BASE_PAYLOAD: Dict[str, Any] = {
    "source_item_id": 1,
    "language_code": "en",
    "slug": "metadata-item",
    "display_title": "Metadata Item",
    "summary_short": "Summary.",
    "bullets": {"key_claim": "Claim.", "evidence_level": "Evidence.", "objective_impact": "Impact."},
    "canonical_url": "https://example.com/1",
    "source_published_at": "2026-06-15T12:00:00Z",
    "approved_at": "2026-06-20T12:00:00Z",
    "published_at": "2026-07-01T00:00:00Z",
    "downstream_action": "publish_summary",
    "disclosure_note": "This item is AI-assisted and human-curated.",
    "author_metadata": {"source_module": "edit", "writer_type": "human", "editor": "john_doe"},
}

# (case name, author_metadata value at the payload level, error token)
INVALID_METADATA_CASES: List[Tuple[str, Any, str]] = [
    ("null", None, "author_metadata"),
    ("json_string", "just a string", "author_metadata"),
    ("json_array", ["edit", "human"], "author_metadata"),
    ("json_number", 5, "author_metadata"),
    ("missing_source_module", {"writer_type": "machine"}, "source_module"),
    ("missing_writer_type", {"source_module": "edit"}, "writer_type"),
    ("unknown_writer_type", {"source_module": "edit", "writer_type": "bot"}, "writer_type"),
    ("source_module_null", {"source_module": None, "writer_type": "machine"}, "source_module"),
    ("source_module_number", {"source_module": 5, "writer_type": "machine"}, "source_module"),
    ("source_module_empty", {"source_module": "", "writer_type": "machine"}, "source_module"),
    ("source_module_whitespace", {"source_module": "   ", "writer_type": "machine"}, "source_module"),
    ("human_editor_missing", {"source_module": "edit", "writer_type": "human"}, "editor"),
    ("human_editor_null", {"source_module": "edit", "writer_type": "human", "editor": None}, "editor"),
    ("human_editor_number", {"source_module": "edit", "writer_type": "human", "editor": 7}, "editor"),
    ("human_editor_empty", {"source_module": "edit", "writer_type": "human", "editor": ""}, "editor"),
    ("human_editor_whitespace", {"source_module": "edit", "writer_type": "human", "editor": "  "}, "editor"),
    ("hybrid_editor_missing", {"source_module": "edit", "writer_type": "hybrid"}, "editor"),
    ("hybrid_editor_null", {"source_module": "edit", "writer_type": "hybrid", "editor": None}, "editor"),
    ("hybrid_editor_number", {"source_module": "edit", "writer_type": "hybrid", "editor": 7}, "editor"),
    ("hybrid_editor_empty", {"source_module": "edit", "writer_type": "hybrid", "editor": ""}, "editor"),
    ("hybrid_editor_whitespace", {"source_module": "edit", "writer_type": "hybrid", "editor": "\t "}, "editor"),
]

# (case name, serialized author_metadata stored in the canonical TEXT column)
E2E_METADATA_CASES: List[Tuple[str, Any]] = [
    ("db_null", None),
    ("malformed_json", "not json at all"),
    ("json_string", '"just a string"'),
    ("json_array", '["edit", "human"]'),
    ("json_number", "5"),
    ("missing_source_module", '{"writer_type": "machine"}'),
    ("missing_writer_type", '{"source_module": "edit"}'),
    ("unknown_writer_type", '{"source_module": "edit", "writer_type": "bot"}'),
    ("source_module_null", '{"source_module": null, "writer_type": "machine"}'),
    ("source_module_number", '{"source_module": 5, "writer_type": "machine"}'),
    ("source_module_empty", '{"source_module": "", "writer_type": "machine"}'),
    ("source_module_whitespace", '{"source_module": "   ", "writer_type": "machine"}'),
    ("human_editor_missing", '{"source_module": "edit", "writer_type": "human"}'),
    ("human_editor_null", '{"source_module": "edit", "writer_type": "human", "editor": null}'),
    ("human_editor_number", '{"source_module": "edit", "writer_type": "human", "editor": 7}'),
    ("human_editor_empty", '{"source_module": "edit", "writer_type": "human", "editor": ""}'),
    ("human_editor_whitespace", '{"source_module": "edit", "writer_type": "human", "editor": "   "}'),
    ("hybrid_editor_number", '{"source_module": "edit", "writer_type": "hybrid", "editor": 7}'),
    ("hybrid_editor_whitespace", '{"source_module": "edit", "writer_type": "hybrid", "editor": "  "}'),
]


class TestAuthorMetadataValidatorMatrix(unittest.TestCase):
    """Direct validate_item_payload() matrix over invalid author_metadata shapes."""

    def test_invalid_metadata_rejected_with_field_context(self) -> None:
        for case_name, metadata, error_token in INVALID_METADATA_CASES:
            with self.subTest(case=case_name):
                payload = copy.deepcopy(BASE_PAYLOAD)
                payload["author_metadata"] = metadata
                with self.assertRaises(ValidationError) as ctx:
                    validate_item_payload(payload)
                self.assertIn(error_token, str(ctx.exception))

    def test_valid_writer_type_shapes_accepted(self) -> None:
        valid_cases = [
            ("human_with_editor", {"source_module": "edit", "writer_type": "human", "editor": "jane"}),
            ("hybrid_with_editor", {"source_module": "curate", "writer_type": "hybrid", "editor": " jane "}),
            ("ai_without_editor", {"source_module": "edit", "writer_type": "AI"}),
            ("machine_without_editor", {"source_module": "translate", "writer_type": "machine"}),
        ]
        for case_name, metadata in valid_cases:
            with self.subTest(case=case_name):
                payload = copy.deepcopy(BASE_PAYLOAD)
                payload["author_metadata"] = metadata
                validate_item_payload(payload)  # must not raise

    def test_disclosure_note_mapping(self) -> None:
        cases = [
            ({"writer_type": "human"}, "This item is AI-assisted and human-curated."),
            ({"writer_type": "hybrid"}, "This item is AI-assisted and human-curated."),
            ({"writer_type": "AI"}, "This item is AI-generated."),
            ({"writer_type": "machine"}, "This item is AI-generated."),
        ]
        for metadata, expected_note in cases:
            with self.subTest(writer_type=metadata["writer_type"]):
                self.assertEqual(expected_note, get_disclosure_note(metadata))


class TestAuthorMetadataEndToEnd(unittest.TestCase):
    """Every invalid case must fail before any artifact or publish state exists."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_first_publish_leaves_no_state_for_any_invalid_case(self) -> None:
        for index, (case_name, db_value) in enumerate(E2E_METADATA_CASES):
            item_id = 100 + index
            with self.subTest(case=case_name):
                support.seed_item(
                    self.db_path, item_id, f"Metadata Item {item_id}", "2026-06-15T12:00:00Z",
                    author_metadata=db_value,
                )
                with self.assertRaises(ValidationError):
                    support.run_publish(self.config, self.db_path, self.export_dir)

                conn = get_connection(self.db_path)
                repo = PublishRepository(conn)
                self.assertIsNone(
                    repo.get_publish_record_by_source_item_id(item_id),
                    f"{case_name} must not leave a publish_record",
                )
                conn.close()

                # The failed run must not establish a live pointer, so no
                # public artifact of the item can exist for any language.
                self.assertFalse(
                    (self.export_dir / "current.json").exists(),
                    f"{case_name} must not produce a live pointer",
                )

    def test_invalid_update_preserves_existing_publication(self) -> None:
        support.seed_item(self.db_path, 1, "Metadata Item", "2026-06-15T12:00:00Z")
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 2)

        slug = "en-metadata-item"
        zh_item_path = support.live_root(self.export_dir) / "zh" / "items" / f"{slug}.json"
        zh_bytes_before = zh_item_path.read_bytes()

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        status_before = dict(repo.get_publish_language_status(pub_rec["publish_record_id"], "zh"))
        conn.close()

        # Turn the metadata invalid (non-string editor) and bump fingerprints
        # so the item would otherwise be republished.
        conn = get_connection(self.db_path)
        conn.execute("""
            UPDATE approved_content_record
            SET author_metadata = '{"source_module": "edit", "writer_type": "human", "editor": 7}',
                content_fingerprint = 'fp_v2'
            WHERE source_item_id = 1
        """)
        conn.execute("UPDATE translation_output SET source_fingerprint = 'fp_v2' WHERE source_item_id = 1")
        conn.commit()
        conn.close()

        with self.assertRaises(ValidationError):
            support.run_publish(self.config, self.db_path, self.export_dir)

        # Original artifact and DB state survive the failed update.
        self.assertEqual(zh_bytes_before, zh_item_path.read_bytes())
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        status_after = dict(repo.get_publish_language_status(pub_rec["publish_record_id"], "zh"))
        conn.close()
        self.assertEqual("published", status_after["publish_status"])
        self.assertEqual(status_before["source_fingerprint"], status_after["source_fingerprint"])
        self.assertEqual(status_before["published_at"], status_after["published_at"])


if __name__ == "__main__":
    unittest.main()
