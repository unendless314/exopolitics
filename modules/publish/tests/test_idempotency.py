"""
Direct idempotency tests for unchanged incremental reruns (plan section 3.4,
EXECUTION_POLICY.md section 5).

Two consecutive incremental runs against identical upstream state must leave
every immutable observation unchanged: publish timestamps, publish record
``updated_at``, language status timestamps, frozen slugs, and the bytes of
every artifact except ``stats.json`` (whose ``last_export_run_timestamp`` is
a volatile per-run field by contract).
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

    def snapshot_artifact_bytes(self):
        return {
            p.relative_to(self.export_dir): p.read_bytes()
            for p in self.export_dir.rglob("*.json")
            if p.name != "stats.json"
        }

    def test_unchanged_rerun_preserves_state_and_bytes(self) -> None:
        clock = support.FakeClock("2026-07-01T00:00:00Z")
        with clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")

            summary1 = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary1["published_count"], 4)
            first_run_timestamp = clock.now_iso

            records_before, statuses_before = self.snapshot_publish_db_state()
            bytes_before = self.snapshot_artifact_bytes()
            stats_before = support.read_stats(self.export_dir)
            self.assertEqual(stats_before["last_export_run_timestamp"], first_run_timestamp)

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

            # Every artifact except volatile stats.json is byte-identical.
            self.assertEqual(bytes_before, self.snapshot_artifact_bytes())

            # stats.json: only last_export_run_timestamp may change; it must
            # track the second run exactly and stay ISO-8601 UTC.
            stats_after = support.read_stats(self.export_dir)
            self.assertEqual(stats_after["last_export_run_timestamp"], second_run_timestamp)
            self.assertRegex(stats_after["last_export_run_timestamp"], ISO_8601_UTC_RE)
            for key in stats_before:
                if key != "last_export_run_timestamp":
                    self.assertEqual(stats_before[key], stats_after[key], key)

            # The frozen publish timestamp inside item JSON still reflects the
            # first run, never the rerun.
            item = support.read_item(self.export_dir, "zh", "en-june-item")
            self.assertEqual(item["published_at"], first_run_timestamp)


if __name__ == "__main__":
    unittest.main()
