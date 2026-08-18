"""
Direct idempotency tests for unchanged incremental reruns (plan section 3.4,
EXECUTION_POLICY.md section 5).

Two consecutive incremental runs against identical upstream state must leave
every immutable observation unchanged: publish timestamps, publish record
``updated_at``, language status timestamps, frozen slugs, and the bytes of
every artifact of the live generation — including ``stats.json``, whose
``last_export_run_timestamp`` is frozen at the generation's build time
(Phase B1: a no-change run builds nothing). The only signal that advances is
the pointer's ``last_successful_run_at``.
"""

import pathlib
import re
import tempfile
import unittest

from modules.publish.src.database import (
    run_migrations,
    get_connection,
)
from modules.publish.tests import support

ISO_8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TestUnchangedRerunIdempotency(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def snapshot_publish_db_state(self):
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT source_item_id, slug, first_published_at, created_at, updated_at
                FROM publish_record ORDER BY source_item_id
            """)
            publish_records = [dict(row) for row in cursor.fetchall()]
            cursor.execute("""
                SELECT pr.source_item_id, pls.language_code, pls.publish_status,
                       pls.published_at, pls.withdrawn_at, pls.source_fingerprint
                FROM publish_language_status pls
                JOIN publish_record pr ON pr.publish_record_id = pls.publish_record_id
                ORDER BY pr.source_item_id, pls.language_code
            """)
            language_statuses = [dict(row) for row in cursor.fetchall()]
            return publish_records, language_statuses
        finally:
            conn.close()

    def snapshot_live_generation_bytes(self):
        live = support.live_root(self.export_dir)
        return {p.relative_to(live): p.read_bytes() for p in live.rglob("*.json")}

    def test_unchanged_rerun_preserves_state_and_bytes(self) -> None:
        clock = support.FakeClock("2026-07-01T00:00:00Z")
        with clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")

            summary1 = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary1["published_count"], 4)
            first_run_timestamp = clock.now_iso

            records_before, statuses_before = self.snapshot_publish_db_state()
            bytes_before = self.snapshot_live_generation_bytes()
            pointer_before = support.read_pointer(self.export_dir)
            stats_before = support.read_stats(self.export_dir)
            self.assertEqual(stats_before["last_export_run_timestamp"], first_run_timestamp)
            self.assertEqual(pointer_before["export_completed_at"], first_run_timestamp)
            self.assertEqual(pointer_before["last_successful_run_at"], first_run_timestamp)

            # Second run against unchanged upstream state, one hour later.
            clock.advance(hours=1)
            summary2 = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary2["published_count"], 0)
            self.assertEqual(summary2["withdrawn_count"], 0)
            second_run_timestamp = clock.now_iso
            self.assertNotEqual(first_run_timestamp, second_run_timestamp)

            # Database publish state is completely frozen.
            records_after, statuses_after = self.snapshot_publish_db_state()
            self.assertEqual(records_before, records_after)
            self.assertEqual(statuses_before, statuses_after)

            # No new generation is built: every artifact byte, stats.json
            # included, is identical and the generation id is unchanged.
            self.assertEqual(bytes_before, self.snapshot_live_generation_bytes())
            pointer_after = support.read_pointer(self.export_dir)
            self.assertEqual(pointer_before["generation"], pointer_after["generation"])
            self.assertEqual(pointer_before["export_completed_at"], pointer_after["export_completed_at"])
            self.assertEqual(pointer_before["content_fingerprint"], pointer_after["content_fingerprint"])

            # stats.json stays frozen at the generation's build time; the run
            # freshness signal lives on the pointer instead (Phase B1).
            stats_after = support.read_stats(self.export_dir)
            self.assertEqual(stats_after["last_export_run_timestamp"], first_run_timestamp)
            self.assertEqual(pointer_after["last_successful_run_at"], second_run_timestamp)
            self.assertRegex(pointer_after["last_successful_run_at"], ISO_8601_UTC_RE)

            # The frozen publish timestamp inside item JSON still reflects the
            # first run, never the rerun.
            item = support.read_item(self.export_dir, "zh", "en-june-item")
            self.assertEqual(item["published_at"], first_run_timestamp)


if __name__ == "__main__":
    unittest.main()
