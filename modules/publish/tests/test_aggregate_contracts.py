"""
Aggregate artifact contract tests for archives/index.json (manifest) and
stats.json (plan section 3.9, DATA_CONTRACT.md sections 6.4 and 6.5).

The manifest must list every non-empty month sorted DESC with the exact file
name, item count and the publish-owned logical write timestamp of that
archive's most recent content change. stats.json must expose every configured
language key with exact counts, including zero values and null oldest
months. All clock-sensitive assertions use an injected FakeClock.
"""

import pathlib
import re
import tempfile
import unittest
from unittest.mock import patch

from modules.publish.src.database import (
    run_migrations,
    get_connection,
)
from modules.publish.tests import support

ISO_8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TestAggregateArtifactContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)
        self.clock = support.FakeClock("2026-07-01T00:00:00Z")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_base_items(self) -> None:
        support.seed_item(self.db_path, 1, "April Item", "2026-04-10T12:00:00Z")
        support.seed_item(self.db_path, 2, "May Item One", "2026-05-15T12:00:00Z")
        support.seed_item(self.db_path, 3, "May Item Two", "2026-05-20T12:00:00Z")

    def set_curation(self, item_id: int, status: str) -> None:
        conn = get_connection(self.db_path)
        conn.execute("UPDATE curation_decision SET curate_status = ? WHERE source_item_id = ?", (status, item_id))
        conn.commit()
        conn.close()

    def manifest_by_month(self, lang: str) -> dict:
        return {e["archive_month"]: e for e in support.read_manifest(self.export_dir, lang)}

    def test_manifest_contract_fields_and_ordering(self) -> None:
        with self.clock.patch():
            self.seed_base_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            run_ts = self.clock.now_iso

            live = support.live_root(self.export_dir)
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    manifest = support.read_manifest(self.export_dir, lang)
                    self.assertEqual(["2026-05", "2026-04"], [e["archive_month"] for e in manifest])

                    may = manifest[0]
                    self.assertEqual("archive_2026_05.json", may["file_name"])
                    self.assertEqual(2, may["item_count"])
                    self.assertEqual(run_ts, may["updated_at"])
                    self.assertRegex(may["updated_at"], ISO_8601_UTC_RE)

                    april = manifest[1]
                    self.assertEqual("archive_2026_04.json", april["file_name"])
                    self.assertEqual(1, april["item_count"])
                    self.assertEqual(run_ts, april["updated_at"])

                    # Every manifest month has a real archive file on disk.
                    for entry in manifest:
                        self.assertTrue(
                            (live / lang / "archives" / entry["file_name"]).exists(),
                            entry["file_name"],
                        )

    def test_manifest_updated_at_lifecycle(self) -> None:
        with self.clock.patch():
            self.seed_base_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            t0 = self.clock.now_iso

            # A new month's archive is created; untouched months keep T0.
            self.clock.advance(hours=1)
            support.seed_item(self.db_path, 4, "June Item", "2026-06-01T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            t1 = self.clock.now_iso

            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t1, months["2026-06"]["updated_at"])
                    self.assertEqual(1, months["2026-06"]["item_count"])
                    self.assertEqual(t0, months["2026-05"]["updated_at"])
                    self.assertEqual(t0, months["2026-04"]["updated_at"])

            # Withdrawal-driven rewrite updates only the affected month.
            self.clock.advance(hours=1)
            self.set_curation(2, "withdrawn")
            support.run_publish(self.config, self.db_path, self.export_dir)
            t2 = self.clock.now_iso

            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t2, months["2026-05"]["updated_at"])
                    self.assertEqual(1, months["2026-05"]["item_count"])
                    self.assertEqual(t1, months["2026-06"]["updated_at"])
                    self.assertEqual(t0, months["2026-04"]["updated_at"])

            # Emptying the May archive removes the file from the new live
            # generation and deletes the metadata row.
            self.clock.advance(hours=1)
            self.set_curation(3, "withdrawn")
            support.run_publish(self.config, self.db_path, self.export_dir)
            t3 = self.clock.now_iso

            live = support.live_root(self.export_dir)
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    self.assertFalse((live / lang / "archives" / "archive_2026_05.json").exists())
                    months = self.manifest_by_month(lang)
                    self.assertNotIn("2026-05", months)
                    self.assertEqual(t1, months["2026-06"]["updated_at"])
                    self.assertEqual(t0, months["2026-04"]["updated_at"])
            conn = get_connection(self.db_path)
            count = conn.execute("SELECT COUNT(*) FROM publish_archive_metadata WHERE archive_month = '2026-05'").fetchone()[0]
            self.assertEqual(0, count)
            conn.close()

            # Recreating the same month's archive starts a fresh timestamp.
            self.clock.advance(hours=1)
            self.set_curation(3, "approved")
            support.run_publish(self.config, self.db_path, self.export_dir)
            t4 = self.clock.now_iso

            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t4, months["2026-05"]["updated_at"])
                    self.assertEqual(1, months["2026-05"]["item_count"])
                    self.assertNotEqual(t2, months["2026-05"]["updated_at"])

            # A full rebuild rewrites every archive and refreshes all timestamps.
            self.clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
            t5 = self.clock.now_iso

            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(["2026-06", "2026-05", "2026-04"], sorted(months, reverse=True))
                    for month in ("2026-04", "2026-05", "2026-06"):
                        self.assertEqual(t5, months[month]["updated_at"], month)

            self.assertGreater(t5, t4)
            self.assertGreater(t4, t3)
            self.assertGreater(t3, t2)
            self.assertGreater(t2, t1)
            self.assertGreater(t1, t0)

    def test_missing_metadata_heals_by_restamping_archive_once(self) -> None:
        """Pre-v002 databases have archives on disk but no
        publish_archive_metadata rows. The next incremental run must stamp
        the metadata once with that run's clock (heal), which changes the
        planned manifest and therefore builds exactly one new generation;
        the archive bytes themselves are unchanged. Once metadata is intact,
        an unchanged run must not build again or advance the stamps
        (DATA_CONTRACT.md section 2.3).
        """
        with self.clock.patch():
            self.seed_base_items()
            support.run_publish(self.config, self.db_path, self.export_dir)

            archive_refs = [
                (lang, month)
                for lang in ("zh", "en")
                for month in ("2026-04", "2026-05")
            ]
            live = support.live_root(self.export_dir)
            bytes_before = {
                ref: (live / ref[0] / "archives" / f"archive_{ref[1].replace('-', '_')}.json").read_bytes()
                for ref in archive_refs
            }

            # Simulate the pre-v002 state: archives exist, metadata is gone.
            conn = get_connection(self.db_path)
            conn.execute("DELETE FROM publish_archive_metadata")
            conn.commit()
            conn.close()

            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            t1 = self.clock.now_iso
            self.assertEqual(0, summary["published_count"])
            self.assertEqual(0, summary["withdrawn_count"])

            # The heal changes the planned manifest timestamps, so exactly one
            # new generation is built; the archive bytes are identical.
            generation_after_heal = support.read_pointer(self.export_dir)["generation"]
            live = support.live_root(self.export_dir)
            for lang, month in archive_refs:
                with self.subTest(language=lang, month=month):
                    self.assertEqual(
                        bytes_before[(lang, month)],
                        (live / lang / "archives" / f"archive_{month.replace('-', '_')}.json").read_bytes(),
                    )
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t1, months["2026-04"]["updated_at"])
                    self.assertEqual(t1, months["2026-05"]["updated_at"])

            # Metadata is now intact: a further unchanged run builds nothing
            # and leaves the generation and its manifest timestamps alone.
            self.clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(
                generation_after_heal,
                support.read_pointer(self.export_dir)["generation"],
            )
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t1, months["2026-04"]["updated_at"])
                    self.assertEqual(t1, months["2026-05"]["updated_at"])

    def test_archive_metadata_converges_after_pointer_switch_failure(self) -> None:
        """A pointer-switch failure after the archive metadata sync leaves
        the DB ahead (the new month stamped with the failed run's clock)
        while the live generation keeps serving the pre-run manifest; the
        next successful run converges both."""
        with self.clock.patch():
            self.seed_base_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            t0 = self.clock.now_iso

            self.clock.advance(hours=1)
            support.seed_item(self.db_path, 4, "June Item", "2026-06-01T12:00:00Z")

            with patch(
                "modules.publish.src.generation_store.write_pointer_atomic",
                side_effect=OSError("Simulated pointer switch failure"),
            ):
                with self.assertRaises(OSError):
                    support.run_publish(self.config, self.db_path, self.export_dir)
            t1 = self.clock.now_iso

            # The DB is ahead and NOT rolled back: the June row exists with
            # this run's clock; untouched months keep T0.
            conn = get_connection(self.db_path)
            rows = conn.execute(
                "SELECT language_code, archive_month, updated_at FROM publish_archive_metadata ORDER BY language_code, archive_month"
            ).fetchall()
            conn.close()
            self.assertEqual(
                [(lang, month, ts) for lang in ("en", "zh") for month, ts in (("2026-04", t0), ("2026-05", t0), ("2026-06", t1))],
                [(r[0], r[1], r[2]) for r in rows],
            )

            # The live generation still serves the pre-failure manifest.
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t0, months["2026-04"]["updated_at"])
                    self.assertEqual(t0, months["2026-05"]["updated_at"])
                    self.assertNotIn("2026-06", months)

            # The next successful run converges: June goes live and the DB
            # metadata stays consistent with the served manifest.
            support.run_publish(self.config, self.db_path, self.export_dir)
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t1, months["2026-06"]["updated_at"])
                    self.assertEqual(1, months["2026-06"]["item_count"])
                    self.assertEqual(t0, months["2026-04"]["updated_at"])

    def test_stats_contract_counts_and_keys(self) -> None:
        config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=2)
        with self.clock.patch():
            self.seed_base_items()
            support.seed_item(self.db_path, 4, "June Item", "2026-06-01T12:00:00Z")
            support.run_publish(config, self.db_path, self.export_dir)
            self.set_curation(4, "withdrawn")
            support.run_publish(config, self.db_path, self.export_dir)
            run_ts = self.clock.now_iso

            stats = support.read_stats(self.export_dir)
            expected_dict_keys = [
                "total_active_published_items_by_language",
                "total_withdrawn_items_by_language",
                "latest_index_count_by_language",
                "archive_month_count_by_language",
                "oldest_archive_month_by_language",
            ]
            for key in expected_dict_keys:
                self.assertIn(key, stats)
                for lang in ("zh", "en"):
                    with self.subTest(stats_key=key, language=lang):
                        self.assertIn(lang, stats[key])

            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    self.assertEqual(3, stats["total_active_published_items_by_language"][lang])
                    self.assertEqual(1, stats["total_withdrawn_items_by_language"][lang])
                    # latest_index_count is min(active_count, latest_limit).
                    self.assertEqual(2, stats["latest_index_count_by_language"][lang])
                    self.assertEqual(2, stats["archive_month_count_by_language"][lang])
                    self.assertEqual("2026-04", stats["oldest_archive_month_by_language"][lang])

            self.assertEqual(run_ts, stats["last_export_run_timestamp"])
            self.assertRegex(stats["last_export_run_timestamp"], ISO_8601_UTC_RE)

    def test_stats_zero_state(self) -> None:
        with self.clock.patch():
            support.run_publish(self.config, self.db_path, self.export_dir)
            run_ts = self.clock.now_iso

            stats = support.read_stats(self.export_dir)
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    self.assertEqual(0, stats["total_active_published_items_by_language"][lang])
                    self.assertEqual(0, stats["total_withdrawn_items_by_language"][lang])
                    self.assertEqual(0, stats["latest_index_count_by_language"][lang])
                    self.assertEqual(0, stats["archive_month_count_by_language"][lang])
                    self.assertIsNone(stats["oldest_archive_month_by_language"][lang])

            self.assertEqual(run_ts, stats["last_export_run_timestamp"])
            self.assertRegex(stats["last_export_run_timestamp"], ISO_8601_UTC_RE)

    def test_zero_state_bootstrap_layout(self) -> None:
        """A zero-data first run still builds a complete (empty) generation:
        every configured language gets an empty index, an empty archives
        manifest and explicit items/ and archives/ directories (Phase B1
        bootstrap layout, consumed by the site loaders)."""
        with self.clock.patch():
            support.run_publish(self.config, self.db_path, self.export_dir)
            run_ts = self.clock.now_iso

            live = support.live_root(self.export_dir)
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    self.assertEqual([], support.read_index(self.export_dir, lang))
                    self.assertEqual([], support.read_manifest(self.export_dir, lang))
                    self.assertTrue((live / lang / "items").is_dir())
                    self.assertTrue((live / lang / "archives").is_dir())

            pointer = support.read_pointer(self.export_dir)
            self.assertEqual(run_ts, pointer["export_completed_at"])
            self.assertEqual(run_ts, pointer["last_successful_run_at"])
            self.assertEqual(["zh", "en"], pointer["languages"])


if __name__ == "__main__":
    unittest.main()
