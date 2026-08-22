"""
Strict-match coverage-loss tests for already-published items (plan section
3.6, STATE_TRANSITIONS.md sections 2 and 3).

If any configured language loses its completed current-fingerprint
translation after publication, every public language artifact of the item
must be withdrawn from the live generation, and restoring coverage must
republish under the same frozen slug. Configured language-set shrink/expand
changes reconcile on the next run (EXECUTION_POLICY.md section 6.2): the new
live generation simply omits the removed language, and retired generations
are reclaimed by retention — never by following symlinks or junctions.
"""

import pathlib
import tempfile
import unittest

from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.tests import support

THREE_LANGUAGES = {"zh": "Traditional Chinese", "en": "English", "ja": "Japanese"}
TWO_LANGUAGES = {"zh": "Traditional Chinese", "en": "English"}


class TestCoverageLossWithdrawal(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(
            target_languages=dict(THREE_LANGUAGES),
            export_dir=self.export_dir,
            batch_size=10,
            latest_limit=5,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_full_coverage_item(self) -> None:
        support.seed_item(
            self.db_path, 1, "Coverage Item", "2026-06-15T12:00:00Z",
            translations={"zh": {}, "en": {}, "ja": {}},
        )

    def get_status(self, repo: PublishRepository, publish_record_id: int, lang: str):
        return repo.get_publish_language_status(publish_record_id, lang)

    def assert_fully_withdrawn(self, slug: str, published_at_by_lang: dict) -> None:
        live = support.live_root(self.export_dir)
        for lang in ("zh", "en", "ja"):
            with self.subTest(language=lang):
                item_file = live / lang / "items" / f"{slug}.json"
                self.assertFalse(item_file.exists(), f"{item_file} must be removed")

                index = support.read_index(self.export_dir, lang)
                self.assertNotIn(slug, {e["slug"] for e in index})

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        for lang in ("zh", "en", "ja"):
            with self.subTest(language=lang):
                status = self.get_status(repo, pub_rec["publish_record_id"], lang)
                self.assertEqual("withdrawn", status["publish_status"])
                self.assertIsNotNone(status["withdrawn_at"])
                # published_at is preserved through withdrawal per contract.
                self.assertEqual(published_at_by_lang[lang], status["published_at"])
        conn.close()

        # The only archive month became empty: the archive file is gone and
        # the manifest is now an empty list (the manifest file itself is
        # always written per contract).
        for lang in ("zh", "en", "ja"):
            with self.subTest(language=lang):
                self.assertFalse((live / lang / "archives" / "archive_2026_06.json").exists())
                self.assertEqual([], support.read_manifest(self.export_dir, lang))

        stats = support.read_stats(self.export_dir)
        for lang in ("zh", "en", "ja"):
            with self.subTest(language=lang):
                self.assertEqual(0, stats["total_active_published_items_by_language"][lang])
                self.assertEqual(1, stats["total_withdrawn_items_by_language"][lang])

    def run_coverage_loss_scenario(self, cause: str) -> None:
        self.seed_full_coverage_item()
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 3)

        slug = "en-coverage-item"
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        published_at_by_lang = {
            lang: self.get_status(repo, pub_rec["publish_record_id"], lang)["published_at"]
            for lang in ("zh", "en", "ja")
        }
        conn.close()

        # Break ja coverage in one of three ways.
        conn = get_connection(self.db_path)
        if cause == "failed_status":
            conn.execute("UPDATE translation_output SET translation_status = 'failed' WHERE source_item_id = 1 AND language_code = 'ja'")
        elif cause == "row_removed":
            conn.execute("DELETE FROM translation_output WHERE source_item_id = 1 AND language_code = 'ja'")
        elif cause == "stale_fingerprint":
            conn.execute("UPDATE translation_output SET source_fingerprint = 'fp_old' WHERE source_item_id = 1 AND language_code = 'ja'")
        else:
            raise AssertionError(f"unknown cause {cause}")
        conn.commit()
        conn.close()

        summary2 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary2["withdrawn_count"], 3)
        self.assert_fully_withdrawn(slug, published_at_by_lang)

        # Restore full coverage with targeted SQL (re-seeding via
        # INSERT OR REPLACE would cascade-delete the publish_record).
        conn = get_connection(self.db_path)
        if cause == "failed_status":
            conn.execute("""
                UPDATE translation_output
                SET translation_status = 'completed',
                    summary_short = 'ja summary for Coverage Item',
                    bullet_1 = 'ja key claim for Coverage Item',
                    bullet_2 = 'ja evidence level for Coverage Item',
                    bullet_3 = 'ja objective impact for Coverage Item'
                WHERE source_item_id = 1 AND language_code = 'ja'
            """)
        elif cause == "row_removed":
            conn.execute("""
                INSERT INTO translation_output (
                    translation_output_id, parent_content_id, source_item_id, language_code,
                    display_title, summary_short, bullet_1, bullet_2, bullet_3,
                    source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
                )
                VALUES (
                    102, 10, 1, 'ja',
                    'Coverage Item', 'ja summary for Coverage Item',
                    'ja key claim for Coverage Item', 'ja evidence level for Coverage Item', 'ja objective impact for Coverage Item',
                    'fp_123', 'completed', 'translator', 'v1', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z'
                )
            """)
        elif cause == "stale_fingerprint":
            conn.execute("UPDATE translation_output SET source_fingerprint = 'fp_123' WHERE source_item_id = 1 AND language_code = 'ja'")
        conn.commit()
        conn.close()

        summary3 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary3["published_count"], 3)

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        self.assertEqual(slug, pub_rec["slug"])
        live = support.live_root(self.export_dir)
        for lang in ("zh", "en", "ja"):
            with self.subTest(language=lang):
                status = self.get_status(repo, pub_rec["publish_record_id"], lang)
                self.assertEqual("published", status["publish_status"])
                # withdrawn_at is retained as audit history after republication.
                self.assertIsNotNone(status["withdrawn_at"])
                self.assertTrue((live / lang / "items" / f"{slug}.json").exists())
        conn.close()

    def test_withdrawal_after_language_fails(self) -> None:
        self.run_coverage_loss_scenario("failed_status")

    def test_withdrawal_after_translation_row_removed(self) -> None:
        self.run_coverage_loss_scenario("row_removed")

    def test_withdrawal_after_fingerprint_goes_stale(self) -> None:
        self.run_coverage_loss_scenario("stale_fingerprint")


class TestConfiguredLanguageSetChanges(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_config(self, languages: dict) -> object:
        return support.make_config(
            target_languages=dict(languages),
            export_dir=self.export_dir,
            batch_size=10,
            latest_limit=5,
        )

    def test_shrink_withdraws_only_removed_language(self) -> None:
        support.seed_item(
            self.db_path, 1, "Shrink Item", "2026-06-15T12:00:00Z",
            translations={"zh": {}, "en": {}, "ja": {}},
        )
        summary = support.run_publish(self.make_config(THREE_LANGUAGES), self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 3)

        slug = "en-shrink-item"
        live = support.live_root(self.export_dir)
        zh_bytes_before = (live / "zh" / "items" / f"{slug}.json").read_bytes()
        en_bytes_before = (live / "en" / "items" / f"{slug}.json").read_bytes()

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        zh_published_at = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")["published_at"]
        en_published_at = repo.get_publish_language_status(pub_rec["publish_record_id"], "en")["published_at"]
        conn.close()

        # Archive metadata of the removed language before the shrink.
        conn = get_connection(self.db_path)
        meta_before = {
            (row[0], row[1]): row[2]
            for row in conn.execute(
                "SELECT language_code, archive_month, updated_at FROM publish_archive_metadata"
            ).fetchall()
        }
        conn.close()
        self.assertIn(("ja", "2026-06"), meta_before)

        # Shrink zh/en/ja -> zh/en: only the ja artifact and status withdraw.
        summary2 = support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)
        self.assertEqual(summary2["published_count"], 0)
        self.assertEqual(summary2["withdrawn_count"], 1)

        live = support.live_root(self.export_dir)
        self.assertFalse((live / "ja").exists())
        self.assertEqual(zh_bytes_before, (live / "zh" / "items" / f"{slug}.json").read_bytes())
        self.assertEqual(en_bytes_before, (live / "en" / "items" / f"{slug}.json").read_bytes())

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        ja_status = repo.get_publish_language_status(pub_rec["publish_record_id"], "ja")
        self.assertEqual("withdrawn", ja_status["publish_status"])
        zh_status = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        en_status = repo.get_publish_language_status(pub_rec["publish_record_id"], "en")
        self.assertEqual("published", zh_status["publish_status"])
        self.assertEqual("published", en_status["publish_status"])
        self.assertEqual(zh_published_at, zh_status["published_at"])
        self.assertEqual(en_published_at, en_status["published_at"])
        conn.close()

        # Remaining languages still list the item in their aggregates.
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                index = support.read_index(self.export_dir, lang)
                self.assertIn(slug, {e["slug"] for e in index})

        self.assert_removed_language_artifacts_gone("ja")

        # Remaining languages keep their archive metadata unchanged.
        conn = get_connection(self.db_path)
        meta_after = {
            (row[0], row[1]): row[2]
            for row in conn.execute(
                "SELECT language_code, archive_month, updated_at FROM publish_archive_metadata"
            ).fetchall()
        }
        conn.close()
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                self.assertEqual(meta_before[(lang, "2026-06")], meta_after[(lang, "2026-06")])

    def assert_removed_language_artifacts_gone(self, removed_lang: str) -> None:
        """A shrunk-away language must not keep serving public artifacts: the
        live generation contains no directory for it, the pointer's language
        list excludes it, and its publish_archive_metadata rows are deleted.
        """
        live = support.live_root(self.export_dir)
        self.assertFalse(
            (live / removed_lang).exists(),
            f"live generation must not contain '{removed_lang}'",
        )
        self.assertNotIn(removed_lang, support.read_pointer(self.export_dir)["languages"])

        conn = get_connection(self.db_path)
        stale_rows = conn.execute(
            "SELECT archive_month FROM publish_archive_metadata WHERE language_code = ?",
            (removed_lang,),
        ).fetchall()
        conn.close()
        self.assertEqual([], stale_rows, "stale metadata rows must be deleted")

    def test_shrink_rebuild_removes_removed_language_artifacts(self) -> None:
        clock = support.FakeClock("2026-07-01T00:00:00Z")
        with clock.patch():
            support.seed_item(
                self.db_path, 1, "Shrink Item", "2026-06-15T12:00:00Z",
                translations={"zh": {}, "en": {}, "ja": {}},
            )
            support.run_publish(self.make_config(THREE_LANGUAGES), self.db_path, self.export_dir)

            # A full rebuild under the shrunk set must reconcile the same way.
            clock.advance(hours=1)
            rebuild_ts = clock.now_iso
            support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir, rebuild=True)

            self.assert_removed_language_artifacts_gone("ja")

            # The rebuild refreshes the remaining languages' metadata with
            # the rebuild run's logical clock.
            conn = get_connection(self.db_path)
            meta_after = {
                (row[0], row[1]): row[2]
                for row in conn.execute(
                    "SELECT language_code, archive_month, updated_at FROM publish_archive_metadata"
                ).fetchall()
            }
            conn.close()
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    self.assertEqual(rebuild_ts, meta_after[(lang, "2026-06")])

    def test_orphan_directory_without_publish_state_is_preserved(self) -> None:
        # Directory names and generic subdirectories (items/, archives/) are
        # not ownership evidence: without publish_language_status rows a
        # directory is not publish's to clean, whatever its shape. Leftovers
        # from a canonical database reset are cleared by wiping the derived
        # export tree, not by heuristic sweeps (EXECUTION_POLICY.md 6.2).
        orphan = self.export_dir / "assets"
        (orphan / "items").mkdir(parents=True)
        (orphan / "archives").mkdir(parents=True)
        (orphan / "index.json").write_text("[]", encoding="utf-8")
        (orphan / "items" / "customer.json").write_text("{}", encoding="utf-8")
        (orphan / "archives" / "archive_2026_01.json").write_text("[]", encoding="utf-8")

        support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)

        for rel_path in (
            "assets/index.json",
            "assets/items/customer.json",
            "assets/archives/archive_2026_01.json",
        ):
            with self.subTest(preserved_file=rel_path):
                self.assertTrue((self.export_dir / rel_path).exists())

    def test_shrink_allows_missing_removed_language_directory(self) -> None:
        """An operator may remove an obsolete language directory from the live
        generation before the configured language set shrinks. The database
        reconciliation must still complete instead of failing while checking
        that absent path.
        """
        import shutil

        support.seed_item(
            self.db_path, 1, "Shrink Item", "2026-06-15T12:00:00Z",
            translations={"zh": {}, "en": {}, "ja": {}},
        )
        support.run_publish(self.make_config(THREE_LANGUAGES), self.db_path, self.export_dir)
        shutil.rmtree(support.live_root(self.export_dir) / "ja")

        summary = support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)
        self.assertEqual(1, summary["withdrawn_count"])

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        self.assertEqual("withdrawn", repo.get_publish_language_status(pub_rec["publish_record_id"], "ja")["publish_status"])
        conn.close()

    def test_unrelated_directory_is_left_alone(self) -> None:
        # A top-level directory that has no publish-owned rows and no publish
        # language-directory structure is not publish's to clean: its files
        # must survive even an otherwise empty run.
        unrelated = self.export_dir / "assets"
        unrelated.mkdir(parents=True)
        (unrelated / "index.json").write_text("{}", encoding="utf-8")
        (unrelated / "data.json").write_text("{}", encoding="utf-8")

        support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)

        for rel_path in ("assets/index.json", "assets/data.json"):
            with self.subTest(preserved_file=rel_path):
                self.assertTrue((self.export_dir / rel_path).exists())

    def test_leftover_language_directory_at_export_root_is_inert(self) -> None:
        # A leftover language-named directory at the export root (outside
        # generations/) is inert: runs neither clean it nor let it leak into
        # the live generation. Root directory names are not ownership
        # evidence (EXECUTION_POLICY.md 6.2); retired generations are
        # reclaimed by retention, not by heuristic sweeps.
        support.seed_item(
            self.db_path, 1, "Shrink Item", "2026-06-15T12:00:00Z",
            translations={"zh": {}, "en": {}, "ja": {}},
        )
        support.run_publish(self.make_config(THREE_LANGUAGES), self.db_path, self.export_dir)
        support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)

        self.assertFalse((support.live_root(self.export_dir) / "ja").exists())

        # The stray directory reappears at the export root (operator litter).
        stray = self.export_dir / "ja"
        stray.mkdir()
        (stray / "index.json").write_text("[]", encoding="utf-8")

        support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)
        self.assertTrue((self.export_dir / "ja" / "index.json").exists())
        self.assertFalse((support.live_root(self.export_dir) / "ja").exists())
        self.assertNotIn("ja", support.read_pointer(self.export_dir)["languages"])

    def test_shrink_converges_after_build_failure(self) -> None:
        """A generation-build failure mid-shrink leaves the DB ahead (ja
        withdrawn, its archive metadata deleted) while the pre-shrink
        generation stays live byte-identically; the next successful run
        converges and the new live generation has no ja artifacts."""
        from unittest.mock import patch

        support.seed_item(
            self.db_path, 1, "Shrink Item", "2026-06-15T12:00:00Z",
            translations={"zh": {}, "en": {}, "ja": {}},
        )
        support.run_publish(self.make_config(THREE_LANGUAGES), self.db_path, self.export_dir)

        live = support.live_root(self.export_dir)
        ja_bytes_before = {
            p.relative_to(live): p.read_bytes()
            for p in sorted((live / "ja").rglob("*.json"))
        }
        zh_index_before = (live / "zh" / "index.json").read_bytes()
        self.assertEqual(4, len(ja_bytes_before))
        pointer_before = support.read_pointer(self.export_dir)

        with patch(
            "modules.publish.src.generation_store.write_generation_to_staging",
            side_effect=OSError("Simulated shrink build failure"),
        ):
            with self.assertRaises(OSError):
                support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)

        # The DB is ahead and NOT rolled back: ja is withdrawn and its
        # metadata rows are gone.
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        ja_status = repo.get_publish_language_status(pub_rec["publish_record_id"], "ja")
        self.assertEqual("withdrawn", ja_status["publish_status"])
        self.assertEqual([], repo.get_archive_metadata_for_language("ja"))
        conn.close()

        # The pre-shrink generation is still live, byte-identical.
        self.assertEqual(pointer_before, support.read_pointer(self.export_dir))
        live_after = support.live_root(self.export_dir)
        for rel_path, content in ja_bytes_before.items():
            with self.subTest(live_artifact=str(rel_path)):
                self.assertEqual(content, (live_after / rel_path).read_bytes())
        self.assertEqual(zh_index_before, (live_after / "zh" / "index.json").read_bytes())

        # The next successful run converges: no ja anywhere in the new live
        # generation.
        support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)
        self.assert_removed_language_artifacts_gone("ja")

    def test_expand_with_incomplete_new_language_withdraws_item(self) -> None:
        # Start fully published under zh/en, with no ja translation at all.
        support.seed_item(self.db_path, 1, "Expand Item", "2026-06-15T12:00:00Z")
        summary = support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 2)

        # Expanding to zh/en/ja makes the item ineligible under strict_match.
        summary2 = support.run_publish(self.make_config(THREE_LANGUAGES), self.db_path, self.export_dir)
        self.assertEqual(summary2["published_count"], 0)
        self.assertEqual(summary2["withdrawn_count"], 2)

        slug = "en-expand-item"
        live = support.live_root(self.export_dir)
        self.assertFalse((live / "zh" / "items" / f"{slug}.json").exists())
        self.assertFalse((live / "en" / "items" / f"{slug}.json").exists())

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        for lang in ("zh", "en"):
            with self.subTest(language=lang):
                status = repo.get_publish_language_status(pub_rec["publish_record_id"], lang)
                self.assertEqual("withdrawn", status["publish_status"])
        conn.close()

    def test_expand_with_complete_new_language_adds_only_that_language(self) -> None:
        support.seed_item(self.db_path, 1, "Expand Item", "2026-06-15T12:00:00Z")
        summary = support.run_publish(self.make_config(TWO_LANGUAGES), self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 2)

        slug = "en-expand-item"
        zh_bytes_before = (support.live_root(self.export_dir) / "zh" / "items" / f"{slug}.json").read_bytes()
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        zh_published_at = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")["published_at"]
        conn.close()

        # Add the completed ja translation, then expand the configured set.
        # (Targeted INSERT only: re-seeding via INSERT OR REPLACE would
        # cascade-delete the publish_record.)
        conn = get_connection(self.db_path)
        conn.execute("""
            INSERT INTO translation_output (
                translation_output_id, parent_content_id, source_item_id, language_code,
                display_title, summary_short, bullet_1, bullet_2, bullet_3,
                source_fingerprint, translation_status, model_name, prompt_version, translated_at, updated_at
            )
            VALUES (
                102, 10, 1, 'ja',
                'Expand Item', 'ja summary for Expand Item',
                'ja key claim for Expand Item', 'ja evidence level for Expand Item', 'ja objective impact for Expand Item',
                'fp_123', 'completed', 'translator', 'v1', '2026-06-20T12:00:00Z', '2026-06-20T12:00:00Z'
            )
        """)
        conn.commit()
        conn.close()
        summary2 = support.run_publish(self.make_config(THREE_LANGUAGES), self.db_path, self.export_dir)
        self.assertEqual(summary2["published_count"], 1)
        self.assertEqual(summary2["withdrawn_count"], 0)

        live = support.live_root(self.export_dir)
        self.assertTrue((live / "ja" / "items" / f"{slug}.json").exists())
        # Existing languages keep their artifacts and publish timestamps.
        self.assertEqual(zh_bytes_before, (live / "zh" / "items" / f"{slug}.json").read_bytes())

        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(1)
        zh_status = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        self.assertEqual("published", zh_status["publish_status"])
        self.assertEqual(zh_published_at, zh_status["published_at"])
        ja_status = repo.get_publish_language_status(pub_rec["publish_record_id"], "ja")
        self.assertEqual("published", ja_status["publish_status"])
        conn.close()


class TestRetentionLinkSafety(unittest.TestCase):
    """Retention must never delete through a symlink or junction, whether the
    retired generation directory itself is a link or it contains one."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_generations(self, count: int) -> list:
        names = [f"2026-07-{day:02d}T00-00-00Z" for day in range(1, count + 1)]
        for name in names:
            generation_dir = self.export_dir / "generations" / name
            generation_dir.mkdir(parents=True)
            (generation_dir / "stats.json").write_text("{}", encoding="utf-8")
        return names

    def make_dir_link(self, link_path: pathlib.Path, target_path: pathlib.Path) -> None:
        import os
        import subprocess

        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
                check=True, capture_output=True,
            )
        else:
            os.symlink(target_path, link_path, target_is_directory=True)

    def test_retention_skips_junction_generation(self) -> None:
        """A retired-generation directory that is itself a junction/symlink
        to a location outside the export tree is skipped with a warning."""
        from modules.publish.src import generation_store

        names = self.seed_generations(7)

        # Replace the oldest generation with a link to an outside target.
        oldest = self.export_dir / "generations" / names[0]
        target_path = pathlib.Path(self.temp_dir.name) / "junction_target"
        import os
        os.rename(oldest, target_path)
        try:
            self.make_dir_link(oldest, target_path)
        except Exception as exc:
            self.skipTest(f"cannot create directory link on this platform: {exc}")

        with self.assertLogs("publish.generation_store", level="WARNING") as logged:
            generation_store.sweep_retired_generations(
                self.export_dir, keep=5, protected_generation=names[-1]
            )

        self.assertTrue(
            any("junction" in m.lower() or "symlink" in m.lower() for m in logged.output),
            logged.output,
        )
        # The link target is untouched and the link itself still exists.
        self.assertEqual("{}", (target_path / "stats.json").read_text(encoding="utf-8"))
        self.assertTrue(oldest.exists())
        # The other retiree (second-oldest) was deleted normally; the five
        # newest stay.
        self.assertFalse((self.export_dir / "generations" / names[1]).exists())
        for name in names[2:]:
            self.assertTrue((self.export_dir / "generations" / name).exists(), name)

    def test_retention_skips_generation_containing_junction(self) -> None:
        """A retired generation that contains a linked subdirectory is
        skipped with a warning as well."""
        from modules.publish.src import generation_store

        names = self.seed_generations(7)

        # The oldest generation is a real directory whose items/ subdir is a
        # link to an outside target.
        oldest = self.export_dir / "generations" / names[0]
        items_link = oldest / "items"
        target_path = pathlib.Path(self.temp_dir.name) / "nested_junction_target"
        target_path.mkdir()
        (target_path / "payload.json").write_text("{}", encoding="utf-8")
        try:
            self.make_dir_link(items_link, target_path)
        except Exception as exc:
            self.skipTest(f"cannot create directory link on this platform: {exc}")

        with self.assertLogs("publish.generation_store", level="WARNING") as logged:
            generation_store.sweep_retired_generations(
                self.export_dir, keep=5, protected_generation=names[-1]
            )

        self.assertTrue(
            any("junction" in m.lower() or "symlink" in m.lower() for m in logged.output),
            logged.output,
        )
        # The link target is untouched and the generation was not deleted.
        self.assertEqual("{}", (target_path / "payload.json").read_text(encoding="utf-8"))
        self.assertTrue(oldest.exists())
        self.assertFalse((self.export_dir / "generations" / names[1]).exists())


if __name__ == "__main__":
    unittest.main()
