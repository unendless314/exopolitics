"""Consumer handoff contract tests pinned to the REAL upstream migrations.

TRANSLATE_TEST_MAINTAINABILITY_PLAN Phase 4 items 1-2 (rationale: section
3.6): translate is a read-only consumer of the ingest/curate handoff surface,
so the assembler contract must be verified against the approved upstream
migration files, not against the minimal isolated-test fixture in
support.py (which only carries the columns the assembler queries).

The temporary DB is built by applying, in dependency order and through
translate's own run_migrations():

1. modules/ingest/src/migrations   (source_item)
2. modules/curate/src/migrations   (curation_decision, curation_output)
3. modules/translate/src/migrations (approved_content_record, translation_output)

Reading other modules' migration SQL here is explicitly endorsed by the plan;
their Python code and their private test helpers are NOT imported.

Deterministic and isolated: temporary SQLite DBs only, no workspace canonical
DB, no .env reads, no network.
"""

import json
import pathlib
import sqlite3
import tempfile
import unittest
from typing import Optional

from modules.translate.src.approved_content_record import (
    assemble_approved_content_records,
    compute_content_fingerprint,
)
from modules.translate.src.database import (
    get_connection,
    run_migrations,
    TranslationRepository,
)
from modules.translate.tests import support

# tests -> translate -> modules
MODULES_ROOT = pathlib.Path(__file__).resolve().parents[2]
INGEST_MIGRATIONS = MODULES_ROOT / "ingest" / "src" / "migrations"
CURATE_MIGRATIONS = MODULES_ROOT / "curate" / "src" / "migrations"
TRANSLATE_MIGRATIONS = MODULES_ROOT / "translate" / "src" / "migrations"

CURATED_AT = "2026-06-20T12:00:00Z"
UPDATED_AT_T1 = "2026-06-21T00:00:00Z"
UPDATED_AT_T2 = "2026-06-21T01:00:00Z"


def build_real_schema_db(db_path: pathlib.Path) -> None:
    """Builds a temporary canonical DB from the approved upstream migration
    contracts (ingest, then curate, then translate) via translate's
    run_migrations()."""
    for migrations_dir in (INGEST_MIGRATIONS, CURATE_MIGRATIONS, TRANSLATE_MIGRATIONS):
        run_migrations(db_path, migrations_dir)


def seed_real_curation_approval(
    conn: sqlite3.Connection,
    *,
    source_item_id: int,
    downstream_action: str,
    display_title: str,
    summary_short: str,
    bullet_1: Optional[str],
    bullet_2: Optional[str],
    bullet_3: Optional[str],
    curated_at: str = CURATED_AT,
    updated_at: str = UPDATED_AT_T1,
) -> None:
    """Seeds one approved curation item against the REAL ingest/curate schema.

    Unlike the minimal-fixture support.seed_curation_approval, the real tables
    carry additional NOT NULL columns and CHECK constraints, so this helper is
    local to the real-schema contract tests:

    - source_item requires fetched_at, ingest_dedup_key (unique per item) and
      dedup_rule (CHECK 'guid'/'url'/'tp'/'fh'); ingest_status is 'ingested'.
    - curation_decision requires decision_actor (CHECK 'system'/'operator')
      and its combo CHECK pairs 'approved' only with downstream_action in
      ('publish_link', 'publish_summary'); decision_actor is 'system'.
    - curation_output matches the minimal fixture.

    downstream_action and all three bullet slots are explicit parameters so
    both legal shapes and the illegal combinations the assembler must reject
    can be constructed.
    """
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO source_item (source_item_id, source_id, title, fetched_at, "
        "ingest_dedup_key, dedup_rule, ingest_status) "
        "VALUES (?, 1, ?, ?, ?, 'guid', 'ingested')",
        (source_item_id, display_title, curated_at, f"dedup-key-{source_item_id}"),
    )
    cursor.execute(
        "INSERT INTO curation_decision (source_item_id, curate_status, downstream_action, "
        "decision_actor, model_name, prompt_version, curated_at, created_at, updated_at) "
        "VALUES (?, 'approved', ?, 'system', 'curator', 'v1', ?, ?, ?)",
        (source_item_id, downstream_action, curated_at, curated_at, curated_at),
    )
    cursor.execute(
        "INSERT INTO curation_output (source_item_id, display_title, summary_short, "
        "bullet_1, bullet_2, bullet_3, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_item_id, display_title, summary_short,
            bullet_1, bullet_2, bullet_3, curated_at, updated_at,
        ),
    )
    conn.commit()


class _RealSchemaBase(unittest.TestCase):
    """Temporary canonical DB built from the real upstream migrations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        build_real_schema_db(self.db_path)
        self.conn = get_connection(self.db_path)
        self.addCleanup(self.conn.close)


class TestHandoffMaterialization(_RealSchemaBase):
    """Phase 4 item 1: approved_content_record materialization contract under
    the real upstream schema — five fields, fingerprint, content language,
    approved_at and the upstream freshness marker."""

    def test_publish_summary_materializes_five_fields_fingerprint_and_language(self) -> None:
        seed_real_curation_approval(
            self.conn,
            source_item_id=10,
            downstream_action="publish_summary",
            display_title="Mother-draft Title One",
            summary_short="This is a brief summary content.",
            bullet_1="Claim content.",
            bullet_2="Evidence content.",
            bullet_3="Impact content.",
            curated_at=CURATED_AT,
            updated_at=UPDATED_AT_T1,
        )

        stats = assemble_approved_content_records(self.conn)
        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(stats["rejected"], 0)

        row = support.snapshot_approved_record(self.conn, source_item_id=10)
        self.assertIsNotNone(row)

        # Straight-through copy of the five content fields: each stored field
        # equals curation_output exactly (no UI presentation labels injected).
        self.assertEqual(row["display_title"], "Mother-draft Title One")
        self.assertEqual(row["summary_short"], "This is a brief summary content.")
        self.assertEqual(row["bullet_1"], "Claim content.")
        self.assertEqual(row["bullet_2"], "Evidence content.")
        self.assertEqual(row["bullet_3"], "Impact content.")
        for field in ("display_title", "summary_short", "bullet_1", "bullet_2", "bullet_3"):
            value = row[field] or ""
            for label in ("Key Claim", "Evidence Level", "Objective Impact"):
                self.assertNotIn(label, value, f"{label} leaked into {field}")

        # Fingerprint is the five-field helper output over the same fields.
        self.assertEqual(
            row["content_fingerprint"],
            compute_content_fingerprint(
                "Mother-draft Title One",
                "This is a brief summary content.",
                "Claim content.",
                "Evidence content.",
                "Impact content.",
            ),
        )

        # Assembler policy: curate-originated mother-drafts materialize with
        # content_language_code 'en', decoupled from any classification
        # language. approved_at is the curation decision's curated_at.
        self.assertEqual(row["content_language_code"], "en")
        self.assertEqual(row["approved_at"], CURATED_AT)

        # The upstream freshness marker is preserved in author_metadata.
        meta = json.loads(row["author_metadata"])
        self.assertEqual(meta["upstream_updated_at"], UPDATED_AT_T1)

    def test_publish_link_materializes_three_null_bullets(self) -> None:
        seed_real_curation_approval(
            self.conn,
            source_item_id=20,
            downstream_action="publish_link",
            display_title="Mother-draft Title Two",
            summary_short="This is a link sharing article.",
            bullet_1=None,
            bullet_2=None,
            bullet_3=None,
        )

        stats = assemble_approved_content_records(self.conn)
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(stats["rejected"], 0)

        row = support.snapshot_approved_record(self.conn, source_item_id=20)
        self.assertIsNotNone(row)
        self.assertEqual(row["display_title"], "Mother-draft Title Two")
        self.assertEqual(row["summary_short"], "This is a link sharing article.")
        self.assertIsNone(row["bullet_1"])
        self.assertIsNone(row["bullet_2"])
        self.assertIsNone(row["bullet_3"])
        self.assertEqual(
            row["content_fingerprint"],
            compute_content_fingerprint(
                "Mother-draft Title Two", "This is a link sharing article.", None, None, None
            ),
        )
        self.assertEqual(row["content_language_code"], "en")
        self.assertEqual(row["approved_at"], CURATED_AT)

    def test_illegal_bullet_shape_rejected_under_real_schema(self) -> None:
        """The Phase 1c zero-trust defense holds against the real schema: a
        publish_summary payload with one NULL bullet is rejected per item, no
        handoff row is written, and the diagnostics name the source item."""
        seed_real_curation_approval(
            self.conn,
            source_item_id=30,
            downstream_action="publish_summary",
            display_title="Partial Bullet Title",
            summary_short="This payload is missing its second bullet.",
            bullet_1="Claim content.",
            bullet_2=None,
            bullet_3="Impact content.",
        )

        stats = assemble_approved_content_records(self.conn)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["inserted"], 0)
        self.assertIsNone(support.snapshot_approved_record(self.conn, source_item_id=30))

        self.assertEqual(len(stats["rejected_items"]), 1)
        diag = stats["rejected_items"][0]
        self.assertEqual(diag["source_item_id"], 30)
        self.assertEqual(diag["downstream_action"], "publish_summary")
        self.assertIn("bullet_2", diag["reason"])


class TestDeltaPrescreen(_RealSchemaBase):
    """Phase 4 item 1 / DATA_CONTRACT.md section 2.1.2: the delta pre-screen
    compares curation_output.updated_at against the freshness marker stored in
    author_metadata.upstream_updated_at (the handoff row's own updated_at is a
    system materialization timestamp and is never compared)."""

    def test_reassembly_skips_when_upstream_marker_not_newer(self) -> None:
        seed_real_curation_approval(
            self.conn,
            source_item_id=40,
            downstream_action="publish_summary",
            display_title="Title A",
            summary_short="Summary text A.",
            bullet_1="Claim A.",
            bullet_2="Evidence A.",
            bullet_3="Impact A.",
            updated_at=UPDATED_AT_T1,
        )
        stats1 = assemble_approved_content_records(self.conn)
        self.assertEqual(stats1["inserted"], 1)
        before = support.snapshot_approved_record(self.conn, source_item_id=40)

        # Unchanged upstream marker: the item is skipped and the handoff row
        # is completely untouched.
        stats2 = assemble_approved_content_records(self.conn)
        self.assertEqual(stats2["skipped"], 1)
        self.assertEqual(stats2["inserted"], 0)
        self.assertEqual(stats2["updated"], 0)
        after = support.snapshot_approved_record(self.conn, source_item_id=40)
        self.assertEqual(after, before)

    def test_newer_upstream_marker_with_content_change_updates_row(self) -> None:
        seed_real_curation_approval(
            self.conn,
            source_item_id=41,
            downstream_action="publish_summary",
            display_title="Title B",
            summary_short="Summary text B.",
            bullet_1="Claim B.",
            bullet_2="Evidence B.",
            bullet_3="Impact B.",
            updated_at=UPDATED_AT_T1,
        )
        assemble_approved_content_records(self.conn)
        before = support.snapshot_approved_record(self.conn, source_item_id=41)

        self.conn.execute(
            "UPDATE curation_output SET display_title = 'Edited Title B', "
            "updated_at = ? WHERE source_item_id = 41",
            (UPDATED_AT_T2,),
        )
        self.conn.commit()

        stats = assemble_approved_content_records(self.conn)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(stats["skipped"], 0)

        after = support.snapshot_approved_record(self.conn, source_item_id=41)
        self.assertEqual(after["display_title"], "Edited Title B")
        self.assertNotEqual(after["content_fingerprint"], before["content_fingerprint"])
        meta = json.loads(after["author_metadata"])
        self.assertEqual(meta["upstream_updated_at"], UPDATED_AT_T2)

    def test_newer_marker_without_content_change_refreshes_metadata_only(self) -> None:
        seed_real_curation_approval(
            self.conn,
            source_item_id=42,
            downstream_action="publish_summary",
            display_title="Title C",
            summary_short="Summary text C.",
            bullet_1="Claim C.",
            bullet_2="Evidence C.",
            bullet_3="Impact C.",
            updated_at=UPDATED_AT_T1,
        )
        assemble_approved_content_records(self.conn)
        before = support.snapshot_approved_record(self.conn, source_item_id=42)

        # Only the upstream timestamp moves; content is identical.
        self.conn.execute(
            "UPDATE curation_output SET updated_at = ? WHERE source_item_id = 42",
            (UPDATED_AT_T2,),
        )
        self.conn.commit()

        # Content-unchanged branch: counted as skipped, but the stored
        # freshness marker is refreshed so later runs keep pre-screening.
        stats = assemble_approved_content_records(self.conn)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["inserted"], 0)

        after = support.snapshot_approved_record(self.conn, source_item_id=42)
        for field in (
            "display_title",
            "summary_short",
            "bullet_1",
            "bullet_2",
            "bullet_3",
            "content_fingerprint",
            "content_language_code",
            "approved_at",
        ):
            self.assertEqual(after[field], before[field], f"{field} changed")
        meta = json.loads(after["author_metadata"])
        self.assertEqual(meta["upstream_updated_at"], UPDATED_AT_T2)


class TestHandoffForeignKeys(_RealSchemaBase):
    """Phase 4 item 1: the real-schema FK chain source_item ->
    approved_content_record -> translation_output cascades on delete."""

    def test_deleting_source_item_cascades_handoff_row(self) -> None:
        seed_real_curation_approval(
            self.conn,
            source_item_id=50,
            downstream_action="publish_summary",
            display_title="Cascade Title",
            summary_short="Cascade summary content.",
            bullet_1="Claim content.",
            bullet_2="Evidence content.",
            bullet_3="Impact content.",
        )
        assemble_approved_content_records(self.conn)
        record = support.snapshot_approved_record(self.conn, source_item_id=50)
        self.assertIsNotNone(record)
        parent_content_id = record["parent_content_id"]

        # A translation row hangs off the handoff row (and the source item).
        support.seed_translation_row(
            self.conn,
            parent_content_id=parent_content_id,
            source_item_id=50,
            language_code="zh",
            display_title="級聯標題",
            summary_short="級聯摘要內容。",
            bullet_1="第一要點內容。",
            bullet_2="第二要點內容。",
            bullet_3="第三要點內容。",
            source_fingerprint=record["content_fingerprint"],
            status="completed",
            retry_count=0,
        )

        self.conn.execute("DELETE FROM source_item WHERE source_item_id = 50")
        self.conn.commit()

        # approved_content_record cascades from source_item; translation_output
        # cascades from approved_content_record.
        self.assertIsNone(support.snapshot_approved_record(self.conn, source_item_id=50))
        remaining = self.conn.execute(
            "SELECT COUNT(*) AS n FROM translation_output WHERE parent_content_id = ?",
            (parent_content_id,),
        ).fetchone()
        self.assertEqual(remaining["n"], 0)


class TestQueueReadContract(_RealSchemaBase):
    """Phase 4 item 1: a materialized handoff record is readable through the
    repository and queues one 'new' task per requested target language."""

    def test_materialized_record_is_readable_and_queued(self) -> None:
        seed_real_curation_approval(
            self.conn,
            source_item_id=60,
            downstream_action="publish_summary",
            display_title="Queue Title",
            summary_short="Queue summary content.",
            bullet_1="Claim content.",
            bullet_2="Evidence content.",
            bullet_3="Impact content.",
        )
        assemble_approved_content_records(self.conn)
        record = support.snapshot_approved_record(self.conn, source_item_id=60)
        self.assertIsNotNone(record)
        parent_content_id = record["parent_content_id"]

        repo = TranslationRepository(self.conn)

        records = repo.get_approved_content_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_item_id"], 60)

        by_id = repo.get_approved_content_by_id(parent_content_id)
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id["source_item_id"], 60)

        by_source = repo.get_approved_content_by_source_id(60)
        self.assertIsNotNone(by_source)
        self.assertEqual(by_source["parent_content_id"], parent_content_id)

        tasks = repo.get_pending_translation_tasks(
            target_languages=["zh", "ja"], retry_attempts=3
        )
        self.assertEqual(len(tasks), 2)
        self.assertEqual({task["language_code"] for task in tasks}, {"zh", "ja"})
        for task in tasks:
            self.assertEqual(task["status"], "new")
            self.assertEqual(task["parent_content_id"], parent_content_id)
            self.assertEqual(task["source_item_id"], 60)
            self.assertEqual(task["display_title"], "Queue Title")
            self.assertEqual(task["summary_short"], "Queue summary content.")
            self.assertEqual(task["bullet_1"], "Claim content.")
            self.assertEqual(task["bullet_2"], "Evidence content.")
            self.assertEqual(task["bullet_3"], "Impact content.")
            self.assertEqual(task["content_fingerprint"], record["content_fingerprint"])
            self.assertEqual(task["content_language_code"], "en")
            self.assertEqual(task["approved_at"], CURATED_AT)


class TestEditOriginatedHandoffPending(_RealSchemaBase):
    """Phase 4 item 2: pending contract for the future edit module handoff."""

    @unittest.skip(
        "edit module not implemented; pending contract per "
        "TRANSLATE_TEST_MAINTAINABILITY_PLAN Phase 4 item 2"
    )
    def test_edit_originated_handoff_materializes_into_shared_table(self) -> None:
        """Future contract: edit-originated handoff materialization.

        The assembler query in approved_content_record.py currently selects
        only curate approvals (see its TODO about the future edit module).
        When the edit module lands, finalized edit_draft outputs must also
        materialize into the shared approved_content_record table with the
        same contract this file pins for curate-originated records: the five
        content fields copied straight through, a five-field
        compute_content_fingerprint, a content_language_code per the assembler
        policy, approved_at from the edit finalization, and the upstream
        freshness marker preserved in author_metadata.

        At that point this test must seed an edit-originated finalized draft
        and assert equivalent materialization (and the delta pre-screen and
        queue read behavior) for the edit path.
        """
        self.fail("edit-originated handoff is not implemented yet")


if __name__ == "__main__":
    unittest.main()
