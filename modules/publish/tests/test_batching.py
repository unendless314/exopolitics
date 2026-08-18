"""
Cross-batch and incremental archive-scope tests (plan section 3.10,
EXECUTION_POLICY.md sections 6.1 and 9).

Index and archive compilation must stay complete, duplicate-free and
correctly ordered when the dataset spans multiple query batches, and an
incremental run must keep byte-identical the monthly archives it did not
affect (their manifest timestamps do not advance). All assertions observe
only exported output and bytes, never internal offsets or SQL call counts.
"""

import pathlib
import tempfile
import unittest

from modules.publish.src.database import (
    run_migrations,
    get_connection,
)
from modules.publish.tests import support


class TestCrossBatchAggregates(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_five_items(self) -> None:
        # Deliberately unsorted seed order with a source-time tie.
        support.seed_item(self.db_path, 3, "Echo Item", "2026-06-10T10:00:00Z")
        support.seed_item(self.db_path, 1, "Alpha Item", "2026-06-10T10:00:00Z")
        support.seed_item(self.db_path, 5, "Golf Item", "2026-06-12T10:00:00Z")
        support.seed_item(self.db_path, 2, "Bravo Item", "2026-06-11T10:00:00Z")
        support.seed_item(self.db_path, 4, "Foxtrot Item", "2026-06-13T10:00:00Z")

    def expected_order(self) -> list:
        return [
            "en-foxtrot-item",  # 2026-06-13
            "en-golf-item",     # 2026-06-12
            "en-bravo-item",    # 2026-06-11
            "en-alpha-item",    # 2026-06-10 tie, slug ASC
            "en-echo-item",     # 2026-06-10 tie, slug ASC
        ]

    def test_cross_batch_index_and_archive_with_batch_size_one(self) -> None:
        config = support.make_config(export_dir=self.export_dir, batch_size=1, latest_limit=4)
        self.seed_five_items()
        support.run_publish(config, self.db_path, self.export_dir)

        expected = self.expected_order()
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                # index.json is truncated to latest_limit after the full sort,
                # paging through four single-row batches.
                index = support.read_index(self.export_dir, lang)
                self.assertEqual(expected[:4], [e["slug"] for e in index])

                # The archive pages through five single-row batches and keeps
                # every item, complete, duplicate-free and ordered.
                archive = support.read_archive(self.export_dir, lang, "2026-06")
                slugs = [e["slug"] for e in archive]
                self.assertEqual(expected, slugs)
                self.assertEqual(len(slugs), len(set(slugs)))

    def test_cross_batch_index_and_archive_with_batch_size_two(self) -> None:
        config = support.make_config(export_dir=self.export_dir, batch_size=2, latest_limit=5)
        self.seed_five_items()
        support.run_publish(config, self.db_path, self.export_dir)

        expected = self.expected_order()
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                index = support.read_index(self.export_dir, lang)
                self.assertEqual(expected, [e["slug"] for e in index])

                archive = support.read_archive(self.export_dir, lang, "2026-06")
                self.assertEqual(expected, [e["slug"] for e in archive])


class TestIncrementalArchiveScope(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_two_months(self) -> None:
        support.seed_item(self.db_path, 1, "May Item", "2026-05-15T12:00:00Z")
        support.seed_item(self.db_path, 2, "June Item", "2026-06-15T12:00:00Z")

    def may_archive_bytes(self, lang: str) -> bytes:
        return (support.live_root(self.export_dir) / lang / "archives" / "archive_2026_05.json").read_bytes()

    def test_unaffected_archive_kept_after_update_in_other_month(self) -> None:
        self.seed_two_months()
        support.run_publish(self.config, self.db_path, self.export_dir)
        may_bytes = {lang: self.may_archive_bytes(lang) for lang in ("zh", "en")}

        # Update only the June item.
        conn = get_connection(self.db_path)
        conn.execute("UPDATE approved_content_record SET content_fingerprint = 'fp_v2' WHERE source_item_id = 2")
        conn.execute("UPDATE translation_output SET source_fingerprint = 'fp_v2' WHERE source_item_id = 2")
        conn.commit()
        conn.close()
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 2)

        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                self.assertEqual(may_bytes[lang], self.may_archive_bytes(lang))

    def test_unaffected_archive_kept_after_withdrawal_in_other_month(self) -> None:
        self.seed_two_months()
        support.run_publish(self.config, self.db_path, self.export_dir)
        may_bytes = {lang: self.may_archive_bytes(lang) for lang in ("zh", "en")}

        # Withdraw only the June item.
        conn = get_connection(self.db_path)
        conn.execute("UPDATE curation_decision SET curate_status = 'withdrawn' WHERE source_item_id = 2")
        conn.commit()
        conn.close()
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["withdrawn_count"], 2)

        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                self.assertEqual(may_bytes[lang], self.may_archive_bytes(lang))
                # Sanity: the June archive was affected and is absent from
                # the new live generation.
                self.assertFalse((support.live_root(self.export_dir) / lang / "archives" / "archive_2026_06.json").exists())


if __name__ == "__main__":
    unittest.main()
