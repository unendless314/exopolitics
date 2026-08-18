import pathlib
import tempfile
import unittest
from unittest.mock import patch

from modules.publish.src.database import (
    run_migrations,
    get_connection,
    PublishRepository,
)
from modules.publish.src.orchestrator import (
    ValidationError,
    slugify,
    generate_slug,
)
from modules.publish.tests import support


class TestPublishModule(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "canonical.db"
        self.export_dir = pathlib.Path(self.temp_dir.name) / "publish_export"

        # Setup tables and run migrations
        support.create_upstream_tables(self.db_path)
        run_migrations(self.db_path, support.PUBLISH_MIGRATIONS_DIR)

        self.config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_data(self, item_id: int, title: str, published_at: str, **kwargs) -> None:
        """Legacy zh/en convenience wrapper; new tests should call support.seed_item directly."""
        support.seed_item(self.db_path, item_id, title, published_at, **kwargs)

    def test_slug_generation_and_freezing(self) -> None:
        """
        1. Test slug creation, collision handling, and slug freezing across later republishes.
        """
        existing = {"hello-world", "hello-world-2"}
        # Should generate hello-world-3
        slug = generate_slug("Hello World!", existing)
        self.assertEqual(slug, "hello-world-3")

        # Test slugify Unicode
        self.assertEqual(slugify("UFO Sighting!"), "ufo-sighting")
        self.assertEqual(slugify("中文"), "") # Empty because no ascii

        # Test database slug freezing
        self.seed_data(1, "Test Article", "2026-06-25T10:00:00Z")
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 2) # en and zh

        # Fetch slug
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        rec = repo.get_publish_record_by_source_item_id(1)
        self.assertIsNotNone(rec)
        first_slug = rec["slug"]
        self.assertEqual(first_slug, "en-test-article") # Slug generated from English display title "EN Test Article"
        conn.close()

        # Update title and fingerprints, but rebuild/run again. Slug must be frozen!
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE approved_content_record SET content_fingerprint = 'fp_456' WHERE source_item_id = 1")
        cursor.execute("UPDATE translation_output SET display_title = 'EN New Title', source_fingerprint = 'fp_456' WHERE source_item_id = 1")
        conn.commit()
        conn.close()

        summary2 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary2["published_count"], 2) # re-published because fingerprint changed
        # Check slug is still same
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        rec2 = repo.get_publish_record_by_source_item_id(1)
        self.assertEqual(rec2["slug"], first_slug)
        conn.close()

    def test_strict_match_eligibility(self) -> None:
        """
        2. Test strict-match eligibility when one language is missing, failed, stale, or fingerprint-mismatched.
        """
        # Case A: Missing translation_output for 'en'
        self.seed_data(2, "Missing English", "2026-06-25T10:00:00Z", translations={"zh": {}, "en": {"status": "pending"}})
        # Delete EN translation output
        conn = get_connection(self.db_path)
        conn.execute("DELETE FROM translation_output WHERE source_item_id = 2 AND language_code = 'en'")
        conn.commit()
        conn.close()

        support.run_publish(self.config, self.db_path, self.export_dir)
        # Should not publish anything for item 2
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNone(repo.get_publish_record_by_source_item_id(2))
        conn.close()

        # Case B: Failed status for 'en'
        self.seed_data(3, "Failed English", "2026-06-25T10:00:00Z", translations={"zh": {}, "en": {"status": "failed"}})
        support.run_publish(self.config, self.db_path, self.export_dir)
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNone(repo.get_publish_record_by_source_item_id(3))
        conn.close()

        # Case C: Stale status for 'en'
        self.seed_data(4, "Stale English", "2026-06-25T10:00:00Z", translations={"zh": {}, "en": {"status": "stale"}})
        support.run_publish(self.config, self.db_path, self.export_dir)
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNone(repo.get_publish_record_by_source_item_id(4))
        conn.close()

        # Case D: Fingerprint mismatch for 'en'
        self.seed_data(5, "Fingerprint Mismatch English", "2026-06-25T10:00:00Z", translations={"zh": {}, "en": {"fingerprint": "fp_old"}})
        support.run_publish(self.config, self.db_path, self.export_dir)
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        self.assertIsNone(repo.get_publish_record_by_source_item_id(5))
        conn.close()

    def test_withdrawal_and_republication(self) -> None:
        """
        3. Withdrawal synchronization when upstream curate_status changes from approved to withdrawn.
        4. Re-publication when a withdrawn item becomes approved again.
        """
        # First publish item 6
        self.seed_data(6, "Item Six", "2026-06-25T10:00:00Z")
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["published_count"], 2)

        # Check files exist in the live generation
        zh_file = support.live_root(self.export_dir) / "zh" / "items" / "en-item-six.json"
        en_file = support.live_root(self.export_dir) / "en" / "items" / "en-item-six.json"
        self.assertTrue(zh_file.exists())
        self.assertTrue(en_file.exists())

        # Check DB status is 'published'
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(6)
        pls_zh = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        self.assertEqual(pls_zh["publish_status"], "published")
        conn.close()

        # Change curate_status to withdrawn via UPDATE to avoid cascade delete of publish_record
        conn = get_connection(self.db_path)
        conn.execute("UPDATE curation_decision SET curate_status = 'withdrawn' WHERE source_item_id = 6")
        conn.commit()
        conn.close()
        summary2 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary2["withdrawn_count"], 2)

        # Check files are absent from the new live generation
        live = support.live_root(self.export_dir)
        self.assertFalse((live / "zh" / "items" / "en-item-six.json").exists())
        self.assertFalse((live / "en" / "items" / "en-item-six.json").exists())

        # Check DB status is 'withdrawn'
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(6)
        pls_zh2 = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        self.assertEqual(pls_zh2["publish_status"], "withdrawn")
        self.assertIsNotNone(pls_zh2["withdrawn_at"])
        # Preserved fingerprint check
        self.assertEqual(pls_zh2["source_fingerprint"], "fp_123")
        conn.close()

        # Re-approve via UPDATE to avoid cascade delete of publish_record
        conn = get_connection(self.db_path)
        conn.execute("UPDATE curation_decision SET curate_status = 'approved' WHERE source_item_id = 6")
        conn.commit()
        conn.close()
        summary3 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary3["published_count"], 2)

        # Check files exist again in the new live generation
        live = support.live_root(self.export_dir)
        self.assertTrue((live / "zh" / "items" / "en-item-six.json").exists())
        self.assertTrue((live / "en" / "items" / "en-item-six.json").exists())

        # Check DB status is 'published' again
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(6)
        pls_zh3 = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        self.assertEqual(pls_zh3["publish_status"], "published")
        conn.close()

    def test_rebuild_and_idempotency(self) -> None:
        """
        5. Rebuild correctness with pre-existing publish rows and frozen slugs.
        6. Idempotent reruns against unchanged database state.
        7. Aggregate file generation excluding withdrawn items.
        """
        self.seed_data(7, "Item Seven", "2026-06-25T10:00:00Z")
        self.seed_data(8, "Item Eight", "2026-06-25T10:00:00Z", curate_status="approved")

        # Run 1: Normal run (publish both)
        summary1 = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary1["published_count"], 4) # 7 and 8 (zh & en)

        # Withdraw Item Eight in database
        conn = get_connection(self.db_path)
        conn.execute("UPDATE curation_decision SET curate_status = 'withdrawn' WHERE source_item_id = 8")
        conn.commit()
        conn.close()

        # Run 2: Idempotent rerun / incremental run (should withdraw Item Eight)
        summary_idemp = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary_idemp["published_count"], 0)
        self.assertEqual(summary_idemp["withdrawn_count"], 2) # Item Eight zh & en

        # Verify index has Item Seven but not Eight
        zh_index = support.read_index(self.export_dir, "zh")
        self.assertEqual(len(zh_index), 1)
        self.assertEqual(zh_index[0]["slug"], "en-item-seven")

        # Run 3: Full Rebuild
        summary_rebuild = support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
        # It should rebuild Item Seven only and not need to withdraw Item Eight again
        self.assertEqual(summary_rebuild["published_count"], 2) # item 7 (en & zh)
        self.assertEqual(summary_rebuild["withdrawn_count"], 0) # already withdrawn in Run 2

        # Check that files exist in the live generation and index still correct
        live = support.live_root(self.export_dir)
        self.assertTrue((live / "zh" / "items" / "en-item-seven.json").exists())
        self.assertFalse((live / "zh" / "items" / "en-item-eight.json").exists())


    def test_archive_withdrawal_and_overlap(self) -> None:
        """
        8. Historical archive withdrawal synchronization.
        9. Monthly archive rebuild correctness (incremental run affected month check).
        10. Latest index and monthly archive overlap consistency.
        """
        # Publish two items in different months
        self.seed_data(9, "June Item", "2026-06-15T12:00:00Z")
        self.seed_data(10, "May Item", "2026-05-15T12:00:00Z")

        support.run_publish(self.config, self.db_path, self.export_dir)

        # Check monthly archives written in the live generation
        live = support.live_root(self.export_dir)
        self.assertTrue((live / "zh" / "archives" / "archive_2026_06.json").exists())
        self.assertTrue((live / "zh" / "archives" / "archive_2026_05.json").exists())

        # Check overlap consistency: June Item is in index.json AND in archive_2026_06.json
        idx = support.read_index(self.export_dir, "zh")
        idx_slugs = {x["slug"] for x in idx}
        self.assertIn("en-june-item", idx_slugs)
        self.assertIn("en-may-item", idx_slugs)

        june_arc = support.read_archive(self.export_dir, "zh", "2026-06")
        self.assertEqual(len(june_arc), 1)
        self.assertEqual(june_arc[0]["slug"], "en-june-item")

        # Now withdraw May Item via UPDATE to avoid cascade delete of publish_record
        conn = get_connection(self.db_path)
        conn.execute("UPDATE curation_decision SET curate_status = 'withdrawn' WHERE source_item_id = 10")
        conn.commit()
        conn.close()
        support.run_publish(self.config, self.db_path, self.export_dir)

        # Check archive_2026_05.json absent from the new live generation (it became empty)
        live = support.live_root(self.export_dir)
        self.assertFalse((live / "zh" / "archives" / "archive_2026_05.json").exists())

        # Check archives index manifest is updated
        manifest = support.read_manifest(self.export_dir, "zh")
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["archive_month"], "2026-06")

        # Validate stats.json
        stats = support.read_stats(self.export_dir)
        self.assertEqual(stats["total_active_published_items_by_language"]["zh"], 1)
        self.assertEqual(stats["total_withdrawn_items_by_language"]["zh"], 1)

    def test_validation_errors(self) -> None:
        """
        Test compilation failures and validation errors for invalid metadata.

        Unique protection (TEST_COVERAGE_MAP.md): CLI validate/migrate/status/
        run/rebuild success surface and status blocked-item counting.
        """
        # Invalid writer_type: hybrid but missing editor
        self.seed_data(11, "Invalid Meta", "2026-06-25T10:00:00Z", author_metadata='{"source_module": "edit", "writer_type": "hybrid"}')
        with self.assertRaises(ValidationError) as ctx:
            support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertIn("editor field is required and must be non-empty when writer_type is 'hybrid'", str(ctx.exception))

        # Assert database was NOT mutated to published for item 11 (prevent divergence)
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(11)
        if pub_rec:
            pls_zh = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
            if pls_zh:
                self.assertNotEqual(pls_zh["publish_status"], "published")
        conn.close()

        # Validate CLI validate command
        from click.testing import CliRunner
        from modules.publish.src.cli import cli

        runner = CliRunner()

        # Write temporary settings file
        temp_yaml = self.export_dir / "settings.yaml"
        temp_yaml.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_yaml, "w", encoding="utf-8") as f:
            f.write("""
target_languages:
  zh: "Traditional Chinese"
  en: "English"
coverage_policy: "strict_match"
execution_policy:
  default_export_dir: "data/publish_export"
  batch_size: 10
index_policy:
  latest_limit: 1000
  archive_granularity: "month"
""")

        # Test CLI validate
        res_val = runner.invoke(cli, ["--config-path", str(temp_yaml), "validate", "--db-path", str(self.db_path)])
        self.assertEqual(res_val.exit_code, 0)

        # Test CLI migrate
        res_mig = runner.invoke(cli, ["--config-path", str(temp_yaml), "migrate", "--db-path", str(self.db_path)])
        self.assertEqual(res_mig.exit_code, 0)

        # Seed an approved item with zero translations to verify blocked counting (Issue 5 fix)
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO source_item (source_item_id, source_id, title, fetched_at, ingest_dedup_key, dedup_rule, ingest_status) VALUES (12, 1, 'Blocked Item', '2026-06-20', 'key_12', 'guid', 'ingested')")
        cursor.execute("INSERT INTO curation_decision (source_item_id, curate_status, decision_actor, model_name, prompt_version, curated_at, created_at, updated_at) VALUES (12, 'approved', 'operator', 'curator', 'v1', '2026-06-20', '2026-06-20', '2026-06-20')")
        conn.commit()
        conn.close()

        # Test CLI status
        res_stat = runner.invoke(cli, ["--config-path", str(temp_yaml), "status", "--db-path", str(self.db_path)])
        self.assertEqual(res_stat.exit_code, 0)
        self.assertIn("PUBLISH STATE PROJECT STATUS SUMMARY", res_stat.output)
        self.assertIn("Blocked Source Items:        1", res_stat.output)


        # Delete invalid item 11 to allow run and rebuild to succeed
        conn = get_connection(self.db_path)
        conn.execute("DELETE FROM source_item WHERE source_item_id = 11")
        conn.commit()
        conn.close()

        # Test CLI run
        res_run = runner.invoke(cli, ["--config-path", str(temp_yaml), "run", "--db-path", str(self.db_path), "--export-dir", str(self.export_dir)])
        self.assertEqual(res_run.exit_code, 0)

        # Test CLI rebuild
        res_reb = runner.invoke(cli, ["--config-path", str(temp_yaml), "rebuild", "--db-path", str(self.db_path), "--export-dir", str(self.export_dir)])
        self.assertEqual(res_reb.exit_code, 0)

    def test_first_time_publish_build_failure_converges(self) -> None:
        """A generation-build failure on a first-time publish keeps the new
        DB state (no compensation) and establishes no live pointer; the next
        successful run converges by state comparison.

        Unique protection (TEST_COVERAGE_MAP.md): first-time publish build
        failure leaves the publish rows intact (DB ahead) and converges on
        rerun."""
        # Seed a new eligible item 15
        self.seed_data(15, "Item Fifteen", "2026-06-25T10:00:00Z")

        with patch(
            "modules.publish.src.generation_store.write_generation_to_staging",
            side_effect=IOError("Disk full"),
        ):
            with self.assertRaises(IOError) as ctx:
                support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertIn("Disk full", str(ctx.exception))

        # No compensation: the new publish rows stay (DB ahead of the export).
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(15)
        self.assertIsNotNone(pub_rec)
        pls = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        self.assertEqual(pls["publish_status"], "published")
        conn.close()

        # No live pointer was established.
        self.assertFalse((self.export_dir / "current.json").exists())

        # The next successful run converges: a generation is built and the
        # item file goes live.
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["status"], "success")
        item = support.read_item(self.export_dir, "zh", "en-item-fifteen")
        self.assertEqual(item["slug"], "en-item-fifteen")

    def test_warning_per_command_scope(self) -> None:
        """Verify target-language warnings are logged once per execution run.

        Unique protection (TEST_COVERAGE_MAP.md): warning count per command
        execution, across two consecutive runs."""
        # Setup settings with a language that doesn't exist in DB translations (e.g. 'ja')
        config = support.make_config(
            target_languages={"zh": "Traditional Chinese", "ja": "Japanese"},
            export_dir=self.export_dir,
            batch_size=10,
            latest_limit=5,
        )

        # Seed translation for zh only, ja is missing
        self.seed_data(16, "Item Sixteen", "2026-06-25T10:00:00Z", translations={"zh": {}, "en": {"status": "pending"}})

        # Call 1
        with self.assertLogs("publish.orchestrator", level="WARNING") as log:
            support.run_publish(config, self.db_path, self.export_dir)
        self.assertEqual(len(log.output), 1)
        self.assertIn("Target language 'ja' has zero completed translations in the database.", log.output[0])

        # Call 2 in same process
        with self.assertLogs("publish.orchestrator", level="WARNING") as log2:
            support.run_publish(config, self.db_path, self.export_dir)
        self.assertEqual(len(log2.output), 1)
        self.assertIn("Target language 'ja' has zero completed translations in the database.", log2.output[0])

    def test_update_build_failure_converges(self) -> None:
        """A build failure on an update run keeps the new DB state and the old
        live generation; the next run rebuilds from the DB state, and a
        further no-change run stays on that generation (fingerprint
        convergence).

        Unique protection (TEST_COVERAGE_MAP.md): update-path build failure
        leaves the DB ahead with the live generation intact, then converges
        without a spurious extra build."""
        clock = support.FakeClock("2026-07-01T00:00:00Z")
        with clock.patch():
            # 1. First, publish an item successfully
            self.seed_data(17, "Item Seventeen", "2026-06-25T10:00:00Z")
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")

            pointer_before = support.read_pointer(self.export_dir)
            zh_item_rel = pathlib.Path("zh") / "items" / "en-item-seventeen.json"
            item_bytes_before = (support.live_root(self.export_dir) / zh_item_rel).read_bytes()

            # 2. Trigger an update by modifying downstream content/fingerprint in DB
            conn = get_connection(self.db_path)
            conn.execute("UPDATE approved_content_record SET content_fingerprint = 'new-fingerprint' WHERE source_item_id = 17")
            conn.execute("UPDATE translation_output SET source_fingerprint = 'new-fingerprint' WHERE parent_content_id = (SELECT parent_content_id FROM approved_content_record WHERE source_item_id = 17)")
            conn.commit()
            conn.close()

            # 3. Fail the generation build of the update run
            clock.advance(hours=1)
            with patch(
                "modules.publish.src.generation_store.write_generation_to_staging",
                side_effect=IOError("Disk full on update"),
            ):
                with self.assertRaises(IOError) as ctx:
                    support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("Disk full on update", str(ctx.exception))

            # The DB is ahead and NOT reverted: the new fingerprint stays.
            conn = get_connection(self.db_path)
            repo = PublishRepository(conn)
            pub_rec = repo.get_publish_record_by_source_item_id(17)
            self.assertIsNotNone(pub_rec)
            pls_after = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
            self.assertEqual(pls_after["publish_status"], "published")
            self.assertEqual(pls_after["source_fingerprint"], "new-fingerprint")
            conn.close()

            # The live generation is untouched.
            self.assertEqual(pointer_before, support.read_pointer(self.export_dir))
            self.assertEqual(
                item_bytes_before,
                (support.live_root(self.export_dir) / zh_item_rel).read_bytes(),
            )

            # 4. Convergence: the next successful run rebuilds from DB state.
            clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            pointer_after = support.read_pointer(self.export_dir)
            self.assertNotEqual(pointer_before["generation"], pointer_after["generation"])
            self.assertNotEqual(
                item_bytes_before,
                (support.live_root(self.export_dir) / zh_item_rel).read_bytes(),
            )

            # 5. A further no-change run stays on that generation: the state
            # comparison converged (no spurious extra build).
            clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(
                pointer_after["generation"],
                support.read_pointer(self.export_dir)["generation"],
            )

    def test_direct_rebuild_after_upstream_withdrawal(self) -> None:
        """Verify direct rebuild after upstream withdrawal without a preceding incremental run."""
        # 1. First, publish successfully
        self.seed_data(18, "Item Eighteen", "2026-06-25T10:00:00Z")
        support.run_publish(self.config, self.db_path, self.export_dir)

        zh_file = support.live_root(self.export_dir) / "zh" / "items" / "en-item-eighteen.json"
        self.assertTrue(zh_file.exists())

        # 2. Update curate_status to withdrawn in database
        conn = get_connection(self.db_path)
        conn.execute("UPDATE curation_decision SET curate_status = 'withdrawn' WHERE source_item_id = 18")
        conn.commit()
        conn.close()

        # 3. Run rebuild directly
        summary = support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
        self.assertEqual(summary["withdrawn_count"], 2) # en and zh
        self.assertEqual(summary["published_count"], 0)

        # 4. Verify item files are absent from the new live generation and DB reflects withdrawn
        live = support.live_root(self.export_dir)
        self.assertFalse((live / "zh" / "items" / "en-item-eighteen.json").exists())
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(18)
        pls_zh = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        self.assertEqual(pls_zh["publish_status"], "withdrawn")
        conn.close()

    def test_failed_rebuild_leaves_live_generation_untouched(self) -> None:
        """A rebuild whose generation build fails must not clear or corrupt
        the currently live generation.

        Unique protection (TEST_COVERAGE_MAP.md): the pre-existing live
        generation survives a failed rebuild unchanged."""
        clock = support.FakeClock("2026-07-01T00:00:00Z")
        with clock.patch():
            # 1. Publish item 19 successfully
            self.seed_data(19, "Item Nineteen", "2026-06-25T10:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)

            pointer_before = support.read_pointer(self.export_dir)
            live = support.live_root(self.export_dir)
            tree_before = {p.relative_to(live): p.read_bytes() for p in live.rglob("*.json")}

            # 2. Fail the generation build during rebuild
            clock.advance(hours=1)
            with patch(
                "modules.publish.src.generation_store.write_generation_to_staging",
                side_effect=IOError("Disk full on rebuild"),
            ):
                with self.assertRaises(IOError) as ctx:
                    support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
            self.assertIn("Disk full on rebuild", str(ctx.exception))

            # 3. The live generation is NOT cleared/half-switched
            self.assertEqual(pointer_before, support.read_pointer(self.export_dir))
            live_after = support.live_root(self.export_dir)
            tree_after = {p.relative_to(live_after): p.read_bytes() for p in live_after.rglob("*.json")}
            self.assertEqual(tree_before, tree_after)

            # 4. A retried rebuild succeeds and switches the pointer.
            clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
            self.assertEqual(summary["status"], "success")
            self.assertNotEqual(
                pointer_before["generation"],
                support.read_pointer(self.export_dir)["generation"],
            )

    def test_archive_index_batching_limit(self) -> None:
        """Verify archive/index behavior with batch_size > latest_limit."""
        # Seed 5 items
        for i in range(20, 25):
            self.seed_data(i, f"Item {i}", f"2026-06-25T10:0{i-20}:00Z")

        # Run with batch_size = 10, latest_limit = 2
        config = support.make_config(export_dir=self.export_dir, batch_size=10, latest_limit=2)

        support.run_publish(config, self.db_path, self.export_dir)

        # Verify index.json has exactly 2 items
        zh_index = support.read_index(self.export_dir, "zh")
        self.assertEqual(len(zh_index), 2)

    def test_pointer_switch_failure_converges(self) -> None:
        """A pointer-switch failure after the generation was built and the
        archive metadata applied leaves the DB ahead with the old generation
        still live; the next successful run converges both.

        Unique protection (TEST_COVERAGE_MAP.md): live-generation byte
        snapshot and pointer stay intact after a pointer-switch failure;
        convergence run rebuilds and switches."""
        clock = support.FakeClock("2026-07-01T00:00:00Z")
        with clock.patch():
            # 1. Publish item 25 successfully
            self.seed_data(25, "Item TwentyFive", "2026-06-25T10:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)

            pointer_before = support.read_pointer(self.export_dir)
            live = support.live_root(self.export_dir)
            zh_item_rel = pathlib.Path("zh") / "items" / "en-item-twentyfive.json"
            orig_item_json = (live / zh_item_rel).read_text(encoding="utf-8")

            # Snapshot the live generation tree; it must survive the failed
            # run byte-identically.
            tree_before = {p.relative_to(live): p.read_bytes() for p in live.rglob("*.json")}

            # 2. Update item 25 and seed item 26, so the run has changes
            conn = get_connection(self.db_path)
            conn.execute("UPDATE approved_content_record SET content_fingerprint = 'new-fp-25' WHERE source_item_id = 25")
            conn.execute("UPDATE translation_output SET source_fingerprint = 'new-fp-25' WHERE parent_content_id = (SELECT parent_content_id FROM approved_content_record WHERE source_item_id = 25)")
            conn.commit()
            conn.close()
            self.seed_data(26, "Item TwentySix", "2026-06-25T10:00:00Z")

            clock.advance(hours=1)
            with patch(
                "modules.publish.src.generation_store.write_pointer_atomic",
                side_effect=OSError("Simulated pointer switch failure"),
            ):
                with self.assertRaises(OSError) as ctx:
                    support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("Simulated pointer switch failure", str(ctx.exception))

            # 3. The DB is ahead and NOT rolled back: item 25 carries the new
            # fingerprint and item 26 is published.
            conn = get_connection(self.db_path)
            repo = PublishRepository(conn)
            pub_rec_25 = repo.get_publish_record_by_source_item_id(25)
            pls_zh_25 = repo.get_publish_language_status(pub_rec_25["publish_record_id"], "zh")
            self.assertEqual(pls_zh_25["source_fingerprint"], "new-fp-25")
            self.assertIsNotNone(repo.get_publish_record_by_source_item_id(26))
            conn.close()

            # 4. The live generation and the pointer are untouched.
            self.assertEqual(pointer_before, support.read_pointer(self.export_dir))
            live_after = support.live_root(self.export_dir)
            self.assertEqual((live_after / zh_item_rel).read_text(encoding="utf-8"), orig_item_json)
            self.assertFalse((live_after / "zh" / "items" / "en-item-twentysix.json").exists())
            tree_after = {p.relative_to(live_after): p.read_bytes() for p in live_after.rglob("*.json")}
            self.assertEqual(tree_before, tree_after)

            # 5. The next successful run converges: rebuild from DB state and
            # switch the pointer; item 26 goes live.
            clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            pointer_after = support.read_pointer(self.export_dir)
            self.assertNotEqual(pointer_before["generation"], pointer_after["generation"])
            live_converged = support.live_root(self.export_dir)
            self.assertTrue((live_converged / "zh" / "items" / "en-item-twentysix.json").exists())
            self.assertNotEqual(
                (live_converged / zh_item_rel).read_text(encoding="utf-8"),
                orig_item_json,
            )


if __name__ == "__main__":
    unittest.main()
