import os
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

        # Check files exist
        zh_file = self.export_dir / "zh" / "items" / "en-item-six.json"
        en_file = self.export_dir / "en" / "items" / "en-item-six.json"
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

        # Check files deleted
        self.assertFalse(zh_file.exists())
        self.assertFalse(en_file.exists())

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

        # Check files exist again
        self.assertTrue(zh_file.exists())
        self.assertTrue(en_file.exists())

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

        # Check that files exist and index still correct
        self.assertTrue((self.export_dir / "zh" / "items" / "en-item-seven.json").exists())
        self.assertFalse((self.export_dir / "zh" / "items" / "en-item-eight.json").exists())


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

        # Check monthly archives written
        june_archive_path = self.export_dir / "zh" / "archives" / "archive_2026_06.json"
        may_archive_path = self.export_dir / "zh" / "archives" / "archive_2026_05.json"
        self.assertTrue(june_archive_path.exists())
        self.assertTrue(may_archive_path.exists())

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

        # Check archive_2026_05.json deleted (as it became empty)
        self.assertFalse(may_archive_path.exists())

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

    @patch("json.dump")
    def test_first_time_file_write_compensation(self, mock_dump) -> None:
        """Verify first-time publish file write failure deletes DB states instead of creating withdrawn.

        Unique protection (TEST_COVERAGE_MAP.md): first-time publish compensation
        removes the newly created publish_record rather than marking it withdrawn."""
        mock_dump.side_effect = IOError("Disk full")

        # Seed a new eligible item 15
        self.seed_data(15, "Item Fifteen", "2026-06-25T10:00:00Z")

        # Run orchestrate_run, it should raise and handle the exception (reverting the DB)
        with self.assertRaises(IOError) as ctx:
            support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertIn("Disk full", str(ctx.exception))

        # Verify no database rows are left for item 15
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(15)
        self.assertIsNone(pub_rec) # Record must be deleted on first-time failure
        conn.close()

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

    @patch("json.dump")
    def test_update_file_write_compensation(self, mock_dump) -> None:
        """Verify that updating an already-published item fails to write file restores previous DB states.

        Unique protection (TEST_COVERAGE_MAP.md): update-path compensation
        restores the prior fingerprint and published status."""
        # 1. First, publish an item successfully
        self.seed_data(17, "Item Seventeen", "2026-06-25T10:00:00Z")
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["status"], "success")

        # Capture the successful database states
        conn = get_connection(self.db_path)
        try:
            repo = PublishRepository(conn)
            pub_rec = repo.get_publish_record_by_source_item_id(17)
            self.assertIsNotNone(pub_rec)
            pls_before = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
            self.assertEqual(pls_before["publish_status"], "published")
            fingerprint_before = pls_before["source_fingerprint"]
        finally:
            conn.close()

        # 2. Trigger an update by modifying downstream content/fingerprint in DB (simulating a change)
        conn = get_connection(self.db_path)
        try:
            # Update fingerprint in approved_content_record and translation_output to trigger a publish update
            conn.execute("UPDATE approved_content_record SET content_fingerprint = 'new-fingerprint' WHERE source_item_id = 17")
            conn.execute("UPDATE translation_output SET source_fingerprint = 'new-fingerprint' WHERE parent_content_id = (SELECT parent_content_id FROM approved_content_record WHERE source_item_id = 17)")
            conn.commit()
        finally:
            conn.close()

        # Mock json.dump to raise IOError when trying to write the updated file
        mock_dump.side_effect = IOError("Disk full on update")

        # Run orchestrate_run, it should raise and handle the exception, reverting DB state to previous published status
        with self.assertRaises(IOError) as ctx:
            support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertIn("Disk full on update", str(ctx.exception))

        # 3. Verify that database rows for item 17 are restored to the state before the failed update
        conn = get_connection(self.db_path)
        try:
            repo = PublishRepository(conn)
            pub_rec_after = repo.get_publish_record_by_source_item_id(17)
            self.assertIsNotNone(pub_rec_after)
            pls_after = repo.get_publish_language_status(pub_rec_after["publish_record_id"], "zh")
            self.assertEqual(pls_after["publish_status"], "published")
            self.assertEqual(pls_after["source_fingerprint"], fingerprint_before) # Fingerprint restored to previous state
        finally:
            conn.close()

    def test_direct_rebuild_after_upstream_withdrawal(self) -> None:
        """Verify direct rebuild after upstream withdrawal without a preceding incremental run."""
        # 1. First, publish successfully
        self.seed_data(18, "Item Eighteen", "2026-06-25T10:00:00Z")
        support.run_publish(self.config, self.db_path, self.export_dir)

        zh_file = self.export_dir / "zh" / "items" / "en-item-eighteen.json"
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

        # 4. Verify item files are deleted and DB reflects withdrawn
        self.assertFalse(zh_file.exists())
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec = repo.get_publish_record_by_source_item_id(18)
        pls_zh = repo.get_publish_language_status(pub_rec["publish_record_id"], "zh")
        self.assertEqual(pls_zh["publish_status"], "withdrawn")
        conn.close()

    @patch("json.dump")
    def test_rebuild_file_write_failure_divergence_prevention(self, mock_dump) -> None:
        """Verify rebuild file write failure does not clear or corrupt final export directory.

        Unique protection (TEST_COVERAGE_MAP.md): pre-existing export files
        survive a failed rebuild unchanged."""
        # 1. Publish item 19 successfully
        self.seed_data(19, "Item Nineteen", "2026-06-25T10:00:00Z")
        support.run_publish(self.config, self.db_path, self.export_dir)

        zh_file = self.export_dir / "zh" / "items" / "en-item-nineteen.json"
        self.assertTrue(zh_file.exists())

        # 2. Mock file writing to fail during rebuild
        mock_dump.side_effect = IOError("Disk full on rebuild")

        # 3. Run rebuild, it should fail
        with self.assertRaises(IOError) as ctx:
            support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
        self.assertIn("Disk full on rebuild", str(ctx.exception))

        # 4. The final export directory should NOT be cleared/half-deleted
        self.assertTrue(zh_file.exists())

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

    def test_promotion_midway_failure_reversion(self) -> None:
        """Verify that a failure midway through file promotion reverts both the export directory and the database.

        Unique protection (TEST_COVERAGE_MAP.md): byte-identical export-tree
        snapshot restore plus DB fingerprint/updated_at restore after a
        promotion-phase failure."""
        # 1. Publish item 25 successfully
        self.seed_data(25, "Item TwentyFive", "2026-06-25T10:00:00Z")
        support.run_publish(self.config, self.db_path, self.export_dir)

        zh_file = self.export_dir / "zh" / "items" / "en-item-twentyfive.json"
        self.assertTrue(zh_file.exists())

        # Keep track of original item JSON to verify it was restored
        orig_item_json = zh_file.read_text(encoding="utf-8")

        # Snapshot the entire export tree; a complete rollback must restore it exactly,
        # regardless of which files happened to be promoted before the failure
        tree_before = {p.relative_to(self.export_dir): p.read_bytes() for p in self.export_dir.rglob("*.json")}

        # Capture database state before update
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec_orig = repo.get_publish_record_by_source_item_id(25)
        pls_orig = repo.get_publish_language_status(pub_rec_orig["publish_record_id"], "zh")
        fingerprint_orig = pls_orig["source_fingerprint"]
        updated_at_orig = pub_rec_orig["updated_at"]
        conn.close()

        # 2. Trigger an update by modifying downstream content/fingerprint in DB
        conn = get_connection(self.db_path)
        conn.execute("UPDATE approved_content_record SET content_fingerprint = 'new-fp-25' WHERE source_item_id = 25")
        conn.execute("UPDATE translation_output SET source_fingerprint = 'new-fp-25' WHERE parent_content_id = (SELECT parent_content_id FROM approved_content_record WHERE source_item_id = 25)")
        conn.commit()
        conn.close()

        # Seed a new item 26 to trigger a run with two items (25 update + 26 publish)
        self.seed_data(26, "Item TwentySix", "2026-06-25T10:00:00Z")

        orig_replace = os.replace
        staging_dir = self.export_dir / ".staging"
        zh_index_dest = self.export_dir / "zh" / "index.json"

        def side_effect(src, dst):
            # Fail deterministically when promotion reaches zh/index.json: with the
            # sorted promotion order, a newly created item-26 file has already been
            # promoted by then, so both rollback paths are exercised. Rollback
            # restores (src in .backup) must pass through or the reversion itself breaks.
            if pathlib.Path(src).is_relative_to(staging_dir) and pathlib.Path(dst) == zh_index_dest:
                raise OSError("Staging promotion disk full simulated error")
            return orig_replace(src, dst)

        with patch("os.replace", side_effect=side_effect):
            with self.assertRaises(OSError) as ctx:
                support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("Staging promotion disk full simulated error", str(ctx.exception))

        # 3. Verify final export dir is restored:
        # - Item 25 should still have its original item JSON (not the updated one)
        # - Item 26 file should NOT exist
        self.assertTrue(zh_file.exists())
        self.assertEqual(zh_file.read_text(encoding="utf-8"), orig_item_json)

        zh_file_26 = self.export_dir / "zh" / "items" / "en-item-twentysix.json"
        self.assertFalse(zh_file_26.exists())

        # The whole export tree must be byte-identical to the pre-failure snapshot
        tree_after = {p.relative_to(self.export_dir): p.read_bytes() for p in self.export_dir.rglob("*.json")}
        self.assertEqual(tree_before, tree_after)

        # 4. Verify DB was rolled back:
        # - Item 25 fingerprint and updated_at in DB should be restored to orig
        # - Item 26 should not be in DB
        conn = get_connection(self.db_path)
        repo = PublishRepository(conn)
        pub_rec_25 = repo.get_publish_record_by_source_item_id(25)
        pls_zh_25 = repo.get_publish_language_status(pub_rec_25["publish_record_id"], "zh")
        self.assertEqual(pls_zh_25["source_fingerprint"], fingerprint_orig)
        self.assertEqual(pub_rec_25["updated_at"], updated_at_orig)

        pub_rec_26 = repo.get_publish_record_by_source_item_id(26)
        self.assertIsNone(pub_rec_26)
        conn.close()


if __name__ == "__main__":
    unittest.main()
