"""
Direct ordering tests for index.json and monthly archives (plan section 3.5,
DATA_CONTRACT.md sections 6.2 and 6.3).

Both aggregates must sort by ``source_published_at DESC`` with a deterministic
``slug ASC`` tiebreaker; the publish-layer ``published_at`` is audit-only and
must never influence ordering or archive month assignment. ``latest_limit``
truncation must happen after the full sort.
"""

import pathlib
import tempfile
import unittest

from modules.publish.src.database import (
    run_migrations,
    get_connection,
)
from modules.publish.tests import support

EXPECTED_ORDER = ["en-newer-title", "en-alpha-title", "en-zulu-title"]


class TestIndexArchiveSorting(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_three_items_with_tie(self) -> None:
        # Two items share the exact same source_published_at; the third is newer.
        support.seed_item(self.db_path, 1, "Zulu Title", "2026-06-10T10:00:00Z")
        support.seed_item(self.db_path, 2, "Alpha Title", "2026-06-10T10:00:00Z")
        support.seed_item(self.db_path, 3, "Newer Title", "2026-06-20T10:00:00Z")

    def test_index_and_archive_sort_by_source_time_then_slug(self) -> None:
        self.seed_three_items_with_tie()
        support.run_publish(self.config, self.db_path, self.export_dir)

        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                index = support.read_index(self.export_dir, lang)
                self.assertEqual(EXPECTED_ORDER, [e["slug"] for e in index])

                archive = support.read_archive(self.export_dir, lang, "2026-06")
                self.assertEqual(EXPECTED_ORDER, [e["slug"] for e in archive])

    def test_sort_uses_source_time_not_publish_time(self) -> None:
        """Republishing the oldest item must not reorder it, and each entry
        keeps source_published_at, approved_at and published_at distinct."""
        clock = support.FakeClock("2026-07-01T00:00:00Z")
        with clock.patch():
            self.seed_three_items_with_tie()
            support.run_publish(self.config, self.db_path, self.export_dir)
            first_run_ts = clock.now_iso

            # Republish only the oldest-tied item one day later.
            clock.advance(days=1)
            second_run_ts = clock.now_iso
            conn = get_connection(self.db_path)
            conn.execute("UPDATE approved_content_record SET content_fingerprint = 'fp_v2' WHERE source_item_id = 1")
            conn.execute("UPDATE translation_output SET source_fingerprint = 'fp_v2' WHERE source_item_id = 1")
            conn.commit()
            conn.close()
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["published_count"], 2)

            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    index = support.read_index(self.export_dir, lang)
                    self.assertEqual(EXPECTED_ORDER, [e["slug"] for e in index])

                    entries = {e["slug"]: e for e in index}
                    zulu = entries["en-zulu-title"]
                    # source timestamp still classifies and sorts the entry...
                    self.assertEqual("2026-06-10T10:00:00Z", zulu["source_published_at"])
                    # ...while the publish-layer audit timestamp refreshed.
                    self.assertEqual(second_run_ts, zulu["published_at"])
                    self.assertEqual(support.DEFAULT_APPROVED_AT, zulu["approved_at"])
                    newer = entries["en-newer-title"]
                    self.assertEqual("2026-06-20T10:00:00Z", newer["source_published_at"])
                    self.assertEqual(first_run_ts, newer["published_at"])

                    archive = support.read_archive(self.export_dir, lang, "2026-06")
                    self.assertEqual(EXPECTED_ORDER, [e["slug"] for e in archive])

    def test_latest_limit_truncates_after_full_sort(self) -> None:
        config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=2)
        self.seed_three_items_with_tie()
        support.run_publish(config, self.db_path, self.export_dir)

        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                index = support.read_index(self.export_dir, lang)
                # Truncation keeps the first entries of the fully sorted list.
                self.assertEqual(EXPECTED_ORDER[:2], [e["slug"] for e in index])

                # The monthly archive is not limited and keeps all three.
                archive = support.read_archive(self.export_dir, lang, "2026-06")
                self.assertEqual(EXPECTED_ORDER, [e["slug"] for e in archive])


if __name__ == "__main__":
    unittest.main()
