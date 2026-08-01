"""
Aggregate artifact contract tests for archives/index.json (manifest) and
stats.json (plan section 3.9, DATA_CONTRACT.md sections 6.4 and 6.5).

The manifest must list every non-empty month sorted DESC with the exact file
name, item count and the publish-owned logical write timestamp of that
archive's most recent write. stats.json must expose every configured
language key with exact counts, including zero values and null oldest
months. All clock-sensitive assertions use an injected FakeClock.
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
                            (self.export_dir / lang / "archives" / entry["file_name"]).exists(),
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

            # Emptying the May archive deletes the file and the metadata row.
            self.clock.advance(hours=1)
            self.set_curation(3, "withdrawn")
            support.run_publish(self.config, self.db_path, self.export_dir)
            t3 = self.clock.now_iso

            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    self.assertFalse((self.export_dir / lang / "archives" / "archive_2026_05.json").exists())
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

    def test_missing_metadata_heals_by_rewriting_archive_once(self) -> None:
        """Pre-v002 databases have archives on disk but no
        publish_archive_metadata rows. The next incremental run must rewrite
        those archives once and stamp the metadata with that run's clock, so
        the manifest updated_at always reflects a real file write
        (DATA_CONTRACT.md section 2.3). Once metadata is intact, an
        unchanged run must not rewrite untouched archives again.
        """
        import os

        with self.clock.patch():
            self.seed_base_items()
            support.run_publish(self.config, self.db_path, self.export_dir)

            # Simulate the pre-v002 state: archives exist, metadata is gone.
            conn = get_connection(self.db_path)
            conn.execute("DELETE FROM publish_archive_metadata")
            conn.commit()
            conn.close()

            archive_paths = [
                self.export_dir / lang / "archives" / f"archive_{month.replace('-', '_')}.json"
                for lang in ("zh", "en")
                for month in ("2026-04", "2026-05")
            ]
            bytes_before = {p: p.read_bytes() for p in archive_paths}
            backdated = 978307200  # 2001-01-01T00:00:00Z
            for p in archive_paths:
                os.utime(p, (backdated, backdated))

            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            t1 = self.clock.now_iso
            self.assertEqual(0, summary["published_count"])
            self.assertEqual(0, summary["withdrawn_count"])

            # The heal run rewrites the file (observable via mtime) with
            # identical content and records the heal run's clock.
            for p in archive_paths:
                with self.subTest(archive=str(p)):
                    self.assertGreater(p.stat().st_mtime, backdated)
                    self.assertEqual(bytes_before[p], p.read_bytes())
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t1, months["2026-04"]["updated_at"])
                    self.assertEqual(t1, months["2026-05"]["updated_at"])

            # Metadata is now intact: a further unchanged run leaves the
            # archives and their timestamps alone.
            for p in archive_paths:
                os.utime(p, (backdated, backdated))
            self.clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            for p in archive_paths:
                with self.subTest(archive=str(p)):
                    self.assertEqual(backdated, int(p.stat().st_mtime))
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t1, months["2026-04"]["updated_at"])
                    self.assertEqual(t1, months["2026-05"]["updated_at"])

    def test_archive_metadata_rolls_back_on_promotion_failure(self) -> None:
        """A promotion-phase failure must restore publish_archive_metadata to
        its pre-run values, alongside the existing DB/file compensation."""
        import os
        from unittest.mock import patch

        with self.clock.patch():
            self.seed_base_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            t0 = self.clock.now_iso

            self.clock.advance(hours=1)
            support.seed_item(self.db_path, 4, "June Item", "2026-06-01T12:00:00Z")

            orig_replace = os.replace
            staging_dir = self.export_dir / ".staging"

            def fail_on_first_promotion(src, dst):
                if pathlib.Path(src).is_relative_to(staging_dir):
                    raise OSError("Simulated promotion failure")
                return orig_replace(src, dst)

            with patch("os.replace", side_effect=fail_on_first_promotion):
                with self.assertRaises(OSError):
                    support.run_publish(self.config, self.db_path, self.export_dir)

            # Metadata is unchanged: no June row, existing rows keep T0.
            conn = get_connection(self.db_path)
            rows = conn.execute(
                "SELECT language_code, archive_month, updated_at FROM publish_archive_metadata ORDER BY language_code, archive_month"
            ).fetchall()
            conn.close()
            self.assertEqual(
                [(lang, month, t0) for lang in ("en", "zh") for month in ("2026-04", "2026-05")],
                [(r[0], r[1], r[2]) for r in rows],
            )

            # The public manifest still shows the pre-failure timestamps.
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    months = self.manifest_by_month(lang)
                    self.assertEqual(t0, months["2026-04"]["updated_at"])
                    self.assertEqual(t0, months["2026-05"]["updated_at"])
                    self.assertNotIn("2026-06", months)

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


if __name__ == "__main__":
    unittest.main()
