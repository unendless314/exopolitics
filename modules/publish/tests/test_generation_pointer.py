"""
Generation + pointer contract tests
(known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md).

Covers the generation/pointer surface not pinned down by the rewritten
pre-refactor tests: bootstrap pointer/meta.json shape, generation id allocation
(including the same-second suffix), pointer write atomicity (sharing-
violation retry and fail-stop), the single-writer process lock, the held
generation-phase SQLite snapshot, retention
(keep-5, live-generation protection, warn-only deletion failures), the
one-time flat-layout migration matrix, fail-stop on a corrupt pointer or
live meta.json, rebuild semantics and meta.json-hash-driven archive
stamping.

Convergence after failed builds/pointer switches is covered by
test_publish.py; no-change rerun byte stability by test_idempotency.py;
junction safety by test_coverage_loss.py.
"""

import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from modules.publish.src import generation_store
from modules.publish.src.database import run_migrations, get_connection
from modules.publish.src.process_lock import ProcessLock
from modules.publish.tests import support

FINGERPRINT_RE = re.compile(r"^sha256-exportstate-v1:[0-9a-f]{64}$")


class PublishB1TestCase(unittest.TestCase):
    """Shared fixture: temp DB + export dir, migrated schema, fake clock."""

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

    def bump_content(self, item_id: int, new_fingerprint: str, new_title: str = None) -> None:
        """Force reconciliation to re-publish an item on the next run.

        With an advanced clock the fingerprint bump alone changes artifact
        bytes (published_at restamps); at the same timestamp a real content
        change (``new_title``) is needed to alter the planned bytes.
        """
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE approved_content_record SET content_fingerprint = ? WHERE source_item_id = ?",
                (new_fingerprint, item_id),
            )
            if new_title is not None:
                conn.execute(
                    "UPDATE translation_output SET display_title = ? WHERE source_item_id = ?",
                    (new_title, item_id),
                )
            conn.execute(
                "UPDATE translation_output SET source_fingerprint = ? WHERE source_item_id = ?",
                (new_fingerprint, item_id),
            )
            conn.commit()
        finally:
            conn.close()

    def generation_names(self) -> list:
        generations_dir = self.export_dir / "generations"
        if not generations_dir.is_dir():
            return []
        return sorted(p.name for p in generations_dir.iterdir())

    def assert_meta_hashes_match(self, generation_root: pathlib.Path) -> dict:
        """meta.json aggregate hashes must cover every aggregate file (never
        item payloads) and match the actual on-disk bytes."""
        meta = support.read_json(generation_root / "meta.json")
        hashes = meta["aggregate_file_hashes"]
        self.assertTrue(hashes, "meta.json must record aggregate file hashes")
        for rel_path, recorded in hashes.items():
            self.assertNotIn("/items/", rel_path)
            digest = hashlib.sha256((generation_root / rel_path).read_bytes()).hexdigest()
            self.assertEqual(recorded, f"sha256:{digest}", rel_path)
        return meta


class TestBootstrapGeneration(PublishB1TestCase):
    """The first successful run always builds a complete generation and
    establishes the pointer (plan section 3, bootstrap path)."""

    def test_first_run_with_data_establishes_pointer_and_meta(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            run_ts = self.clock.now_iso

        expected_id = "2026-07-01T00-00-00Z"
        pointer = support.read_pointer(self.export_dir)
        self.assertEqual(pointer["generation"], expected_id)
        self.assertEqual(pointer["export_completed_at"], run_ts)
        self.assertEqual(pointer["last_successful_run_at"], run_ts)
        self.assertEqual(pointer["languages"], ["zh", "en"])
        self.assertRegex(pointer["content_fingerprint"], FINGERPRINT_RE)

        generation_root = self.export_dir / "generations" / expected_id
        meta = self.assert_meta_hashes_match(generation_root)
        self.assertEqual(meta["generation"], expected_id)
        self.assertEqual(meta["created_at"], run_ts)
        self.assertEqual(meta["content_fingerprint"], pointer["content_fingerprint"])
        self.assertEqual(
            set(meta["aggregate_file_hashes"].keys()),
            {
                "stats.json",
                "zh/index.json",
                "zh/archives/index.json",
                "zh/archives/archive_2026_06.json",
                "en/index.json",
                "en/archives/index.json",
                "en/archives/archive_2026_06.json",
            },
        )

    def test_first_run_without_data_establishes_empty_generation_and_pointer(self) -> None:
        """Zero-state bootstrap: the pointer and meta.json are established
        even though every aggregate is empty (layout details are pinned by
        test_aggregate_contracts.test_zero_state_bootstrap_layout)."""
        with self.clock.patch():
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["published_count"], 0)
            run_ts = self.clock.now_iso

        pointer = support.read_pointer(self.export_dir)
        self.assertRegex(pointer["generation"], generation_store.GENERATION_ID_RE)
        self.assertEqual(pointer["export_completed_at"], run_ts)
        self.assertEqual(pointer["last_successful_run_at"], run_ts)
        self.assertEqual(pointer["languages"], ["zh", "en"])
        self.assertRegex(pointer["content_fingerprint"], FINGERPRINT_RE)

        meta = self.assert_meta_hashes_match(support.live_root(self.export_dir))
        # stats.json + per-language index and (empty) archives manifest.
        self.assertEqual(
            set(meta["aggregate_file_hashes"].keys()),
            {"stats.json", "zh/index.json", "zh/archives/index.json", "en/index.json", "en/archives/index.json"},
        )


class TestGenerationIdAllocation(PublishB1TestCase):
    """Generation ids derive from the single run timestamp; same-second
    builds get a ``-rN`` collision suffix."""

    def test_same_second_builds_get_suffix(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(support.read_pointer(self.export_dir)["generation"], "2026-07-01T00-00-00Z")

            # Same second, real content change: a second build must not
            # overwrite the existing generation directory.
            self.bump_content(1, "fp_2", new_title="June Item Retitled")
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(support.read_pointer(self.export_dir)["generation"], "2026-07-01T00-00-00Z-r2")

            self.bump_content(1, "fp_3", new_title="June Item Retitled Again")
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(support.read_pointer(self.export_dir)["generation"], "2026-07-01T00-00-00Z-r3")

        self.assertEqual(
            self.generation_names(),
            ["2026-07-01T00-00-00Z", "2026-07-01T00-00-00Z-r2", "2026-07-01T00-00-00Z-r3"],
        )


class TestPointerAtomicity(PublishB1TestCase):
    """The pointer switch is a same-volume os.replace over a temp file;
    sharing violations retry a limited number of times, then fail stop with
    the old pointer still valid."""

    def test_pointer_write_retries_sharing_violation_then_succeeds(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)

            self.clock.advance(hours=1)
            real_replace = os.replace
            state = {"calls": 0}

            def flaky_replace(src, dst):
                state["calls"] += 1
                if state["calls"] == 1:
                    raise PermissionError("simulated sharing violation")
                return real_replace(src, dst)

            with patch("os.replace", new=flaky_replace):
                with self.assertLogs("publish.generation_store", level="WARNING") as logged:
                    summary = support.run_publish(self.config, self.db_path, self.export_dir)

            self.assertEqual(summary["status"], "success")
            self.assertEqual(state["calls"], 2)
            self.assertTrue(any("sharing violation" in m for m in logged.output), logged.output)

            pointer = support.read_pointer(self.export_dir)
            self.assertEqual(pointer["generation"], "2026-07-01T00-00-00Z")
            self.assertEqual(pointer["last_successful_run_at"], self.clock.now_iso)

    def test_pointer_write_failure_after_retries_keeps_old_pointer(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            pointer_before = support.read_pointer(self.export_dir)
            live_before = support.live_root(self.export_dir)
            bytes_before = {p.relative_to(live_before): p.read_bytes() for p in live_before.rglob("*.json")}

            self.clock.advance(hours=1)
            with patch("os.replace", side_effect=PermissionError("permanent sharing violation")):
                with self.assertRaises(PermissionError):
                    support.run_publish(self.config, self.db_path, self.export_dir)

            # The old pointer and the live generation are untouched, and the
            # temp file is cleaned up after the final failed attempt.
            self.assertEqual(pointer_before, support.read_pointer(self.export_dir))
            live_after = support.live_root(self.export_dir)
            self.assertEqual(bytes_before, {p.relative_to(live_after): p.read_bytes() for p in live_after.rglob("*.json")})
            self.assertFalse((self.export_dir / ".current.json.tmp").exists())

            # Once the blocker clears, the next run converges normally.
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(
                support.read_pointer(self.export_dir)["last_successful_run_at"],
                self.clock.now_iso,
            )


class TestSingleWriterLock(PublishB1TestCase):
    """The whole run is serialized through publish_runner.lock next to the
    database file (curate/translate precedent)."""

    def test_run_refused_while_lock_is_held_and_lock_file_persists_after_run(self) -> None:
        lock_path = self.db_path.parent / "publish_runner.lock"
        lock = ProcessLock(lock_path)
        lock.acquire()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("Could not acquire lock", str(ctx.exception))
        finally:
            lock.release()

        support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
        summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["status"], "success")
        # A finished run releases the lock but deliberately leaves the lock
        # file in place: unlinking it would let two processes hold locks on
        # different inodes of the same path (inode-reuse race). The path
        # stays directly reusable.
        self.assertTrue(lock_path.exists())
        reacquired = ProcessLock(lock_path)
        reacquired.acquire()
        reacquired.release()


class TestGenerationPhaseSnapshot(PublishB1TestCase):
    """The generation phase (plan, fingerprint pass, write pass) reads one
    held SQLite snapshot, so a generation can never mix pre- and post-update
    DB states. The snapshot is opened with BEGIN IMMEDIATE, reserving the
    writer slot up front: a concurrent upstream writer is rejected at its
    own BEGIN IMMEDIATE instead of starting writes that would interleave or
    doom the build's metadata commit (shared-to-writer upgrade conflict).
    The pipeline runs modules sequentially, so this never contends in normal
    operation."""

    def test_concurrent_upstream_write_during_build_is_excluded(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)

            self.clock.advance(hours=1)
            self.bump_content(1, "fp_snapshot")

            write_attempts = []
            real_write = generation_store.write_generation_to_staging

            def upstream_write_during_build(*args, **kwargs):
                # A second connection simulating curate/translate writing to
                # the same database while the generation phase is mid-build.
                # Its BEGIN IMMEDIATE must be rejected outright: with the
                # writer slot already reserved by the run, it can neither
                # interleave into the snapshot nor later block the metadata
                # commit's lock upgrade.
                other = sqlite3.connect(str(self.db_path), timeout=0.1)
                try:
                    try:
                        other.execute("BEGIN IMMEDIATE")
                    except sqlite3.OperationalError as e:
                        write_attempts.append(f"begin refused: {e}")
                        return real_write(*args, **kwargs)
                    write_attempts.append("begin unexpectedly succeeded")
                    other.rollback()
                    return real_write(*args, **kwargs)
                finally:
                    other.close()

            with patch(
                "modules.publish.src.generation_store.write_generation_to_staging",
                side_effect=upstream_write_during_build,
            ):
                summary = support.run_publish(self.config, self.db_path, self.export_dir)

            # The run completes from the held snapshot; the interleaved writer
            # was refused at BEGIN IMMEDIATE, never reaching the database.
            self.assertEqual(summary["status"], "success")
            self.assertEqual(len(write_attempts), 1)
            self.assertIn("begin refused", write_attempts[0])
            self.assertIn("locked", write_attempts[0].lower())
            self.assertEqual(self.generation_names()[-1], "2026-07-01T01-00-00Z")


class TestRetention(PublishB1TestCase):
    """generations/ keeps the newest 5 generations plus, unconditionally,
    the one the live pointer references; deletion problems are warn-only.
    Junction/symlink safety is covered by test_coverage_loss.py."""

    def run_seven_builds(self) -> list:
        """Seven consecutive content-changing runs, one hour apart; returns
        the generation ids in build order."""
        support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
        ids = [f"2026-07-01T{hour:02d}-00-00Z" for hour in range(7)]
        support.run_publish(self.config, self.db_path, self.export_dir)
        for index in range(1, 7):
            self.clock.advance(hours=1)
            self.bump_content(1, f"fp_{index}")
            support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(self.generation_names()[-1], ids[-1])
        return ids

    def test_retention_keeps_only_five_newest_generations(self) -> None:
        with self.clock.patch():
            ids = self.run_seven_builds()
            self.assertEqual(self.generation_names(), ids[2:])
            self.assertEqual(support.read_pointer(self.export_dir)["generation"], ids[-1])

    def test_retention_never_deletes_live_generation(self) -> None:
        """Even in a pathological ordering where the live generation falls
        outside the newest 5, the protected generation is never deleted."""
        names = [f"2026-07-{day:02d}T00-00-00Z" for day in range(1, 8)]
        for name in names:
            generation_dir = self.export_dir / "generations" / name
            generation_dir.mkdir(parents=True)
            (generation_dir / "stats.json").write_text("{}", encoding="utf-8")

        generation_store.sweep_retired_generations(
            self.export_dir, keep=5, protected_generation=names[0]
        )

        # names[0] is a retiree by age but protected; only names[1] goes.
        self.assertTrue((self.export_dir / "generations" / names[0]).exists())
        self.assertEqual(self.generation_names(), [names[0]] + names[2:])

    def test_retention_deletion_failure_is_warn_only_and_converges_next_run(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            ids = [f"2026-07-01T{hour:02d}-00-00Z" for hour in range(7)]
            for index in range(5):
                if index:
                    self.clock.advance(hours=1)
                    self.bump_content(1, f"fp_{index}")
                support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(self.generation_names(), ids[:5])

            # The sixth build makes ids[0] a retiree; deletion is blocked by
            # a reader holding the files, which must not fail the run.
            self.clock.advance(hours=1)
            self.bump_content(1, "fp_5")
            with patch(
                "modules.publish.src.generation_store.shutil.rmtree",
                side_effect=OSError("locked by a reader"),
            ):
                with self.assertLogs("publish.generation_store", level="WARNING") as logged:
                    summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertTrue(any("Could not delete retired generation" in m for m in logged.output), logged.output)
            self.assertEqual(self.generation_names(), ids[:6])

            # The next run retries and retires the backlog.
            self.clock.advance(hours=1)
            self.bump_content(1, "fp_6")
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(self.generation_names(), ids[2:])

    def test_retention_orders_same_second_suffixes_numerically(self) -> None:
        """A plain string sort would place '-r10' before '-r2' and let
        retention delete newer same-second snapshots; ordering must be by
        the numeric collision suffix."""
        base = "2026-07-01T00-00-00Z"
        names = [base] + [f"{base}-r{index}" for index in range(2, 12)]
        for name in names:
            generation_dir = self.export_dir / "generations" / name
            generation_dir.mkdir(parents=True)
            (generation_dir / "stats.json").write_text("{}", encoding="utf-8")

        generation_store.sweep_retired_generations(
            self.export_dir, keep=5, protected_generation=names[-1]
        )

        remaining = {p.name for p in (self.export_dir / "generations").iterdir()}
        self.assertEqual(remaining, set(names[-5:]))

    def test_same_second_id_allocation_never_refills_retired_gaps(self) -> None:
        """Retired same-second ids stay retired: allocation continues after
        the highest surviving suffix. Reusing a gap id would make a fresh
        generation sort as the oldest, so a later sweep could delete it
        while keeping genuinely older ones."""
        base = "2026-07-01T00-00-00Z"
        generations_dir = self.export_dir / "generations"

        def make(name: str) -> None:
            generation_dir = generations_dir / name
            generation_dir.mkdir(parents=True)
            (generation_dir / "stats.json").write_text("{}", encoding="utf-8")

        # base through -r6 exist; the keep-5 sweep retires base.
        for name in [base] + [f"{base}-r{index}" for index in range(2, 7)]:
            make(name)
        generation_store.sweep_retired_generations(
            self.export_dir, keep=5, protected_generation=f"{base}-r6"
        )
        self.assertFalse((generations_dir / base).exists())

        # The next same-second build continues after the highest surviving
        # suffix instead of reusing the retired base id...
        allocated = generation_store.allocate_generation_id(
            generations_dir, "2026-07-01T00:00:00Z"
        )
        self.assertEqual(allocated, f"{base}-r7")
        make(allocated)

        # ...and the next sweep retires the oldest survivor, never the fresh
        # generation.
        generation_store.sweep_retired_generations(
            self.export_dir, keep=5, protected_generation=allocated
        )
        self.assertTrue((generations_dir / allocated).exists())
        self.assertFalse((generations_dir / f"{base}-r2").exists())


class TestFlatLayoutMigration(PublishB1TestCase):
    """One-time migration of a pre-B1 flat export tree (plan section on
    migration): a byte-exact tree is moved into generations/; anything else
    falls back to a bootstrap build from the DB plan."""

    def deflate_live_generation_to_flat_layout(self) -> None:
        """Turn the post-B1 export root back into the pre-B1 flat layout:
        stats.json + <lang>/ at the root, no pointer, no meta.json, no
        generations/ directory."""
        live = support.live_root(self.export_dir)
        for entry in live.iterdir():
            if entry.name == "meta.json":
                continue
            shutil.move(str(entry), str(self.export_dir / entry.name))
        (self.export_dir / "current.json").unlink()
        shutil.rmtree(self.export_dir / "generations")

    def seed_two_items(self) -> None:
        support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
        support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")

    def test_matching_flat_tree_is_migrated_into_first_generation(self) -> None:
        with self.clock.patch():
            self.seed_two_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            t0 = self.clock.now_iso
            item_bytes_before = support.read_item(self.export_dir, "zh", "en-june-item")

            self.deflate_live_generation_to_flat_layout()
            self.assertTrue((self.export_dir / "stats.json").is_file())
            self.assertTrue((self.export_dir / "zh").is_dir())

            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["published_count"], 0)
            t1 = self.clock.now_iso

            # The generation id and export_completed_at derive from the flat
            # stats' run timestamp; the freshness signal is this run's.
            pointer = support.read_pointer(self.export_dir)
            self.assertEqual(pointer["generation"], "2026-07-01T00-00-00Z")
            self.assertEqual(pointer["export_completed_at"], t0)
            self.assertEqual(pointer["last_successful_run_at"], t1)
            self.assertRegex(pointer["content_fingerprint"], FINGERPRINT_RE)

            # Publish-owned entries moved out of the root; stats.json keeps
            # its original (flat) run timestamp.
            self.assertFalse((self.export_dir / "stats.json").exists())
            self.assertFalse((self.export_dir / "zh").exists())
            self.assertEqual(support.read_stats(self.export_dir)["last_export_run_timestamp"], t0)

            generation_root = support.live_root(self.export_dir)
            meta = self.assert_meta_hashes_match(generation_root)
            self.assertEqual(meta["generation"], "2026-07-01T00-00-00Z")
            self.assertEqual(meta["created_at"], t1)
            self.assertEqual(meta["content_fingerprint"], pointer["content_fingerprint"])

            # Bytes survive the move untouched and archive stamps keep the
            # DB values (no restamping on migration).
            item_after = support.read_item(self.export_dir, "zh", "en-june-item")
            self.assertEqual(item_bytes_before, item_after)
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    for entry in support.read_manifest(self.export_dir, lang):
                        self.assertEqual(entry["updated_at"], t0)

            # A no-change run after migration must not build: the fingerprint
            # converges via the migrated meta.json hashes.
            self.clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            settled = support.read_pointer(self.export_dir)
            self.assertEqual(settled["generation"], "2026-07-01T00-00-00Z")
            self.assertEqual(settled["last_successful_run_at"], self.clock.now_iso)

    def test_flat_tree_with_unowned_directories_keeps_them_in_place(self) -> None:
        """Only the configured language directories and stats.json move;
        anything else (residual language dirs, assets/) stays at the root."""
        with self.clock.patch():
            self.seed_two_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.deflate_live_generation_to_flat_layout()

            (self.export_dir / "ja").mkdir()
            (self.export_dir / "ja" / "index.json").write_text("[]", encoding="utf-8")
            (self.export_dir / "assets").mkdir()
            (self.export_dir / "assets" / "logo.txt").write_text("logo", encoding="utf-8")

            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")

            self.assertEqual(support.read_pointer(self.export_dir)["generation"], "2026-07-01T00-00-00Z")
            self.assertEqual((self.export_dir / "ja" / "index.json").read_text(encoding="utf-8"), "[]")
            self.assertTrue((self.export_dir / "assets" / "logo.txt").exists())
            self.assertFalse((support.live_root(self.export_dir) / "ja").exists())

    def test_db_ahead_of_flat_tree_falls_back_to_bootstrap_build(self) -> None:
        """The old runner could stop between its DB commits and file
        promotion; a flat tree that no longer matches the DB plan is not
        trusted — the first complete generation is built from the DB."""
        with self.clock.patch():
            self.seed_two_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.deflate_live_generation_to_flat_layout()

            # DB runs ahead of the flat tree.
            support.seed_item(self.db_path, 3, "July Item", "2026-07-01T12:00:00Z")

            self.clock.advance(hours=1)
            with self.assertLogs("publish.orchestrator", level="WARNING") as logged:
                summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertTrue(any("does not match the DB plan" in m for m in logged.output), logged.output)

            # Bootstrap build keyed by this run's timestamp, with all items.
            pointer = support.read_pointer(self.export_dir)
            self.assertEqual(pointer["generation"], "2026-07-01T01-00-00Z")
            self.assertEqual(pointer["export_completed_at"], self.clock.now_iso)
            self.assertEqual(support.read_item(self.export_dir, "zh", "en-july-item")["slug"], "en-july-item")

            # The untrusted flat tree is left in place, inert.
            self.assertTrue((self.export_dir / "stats.json").exists())
            self.assertTrue((self.export_dir / "zh").exists())

    def test_flat_tree_missing_artifact_falls_back_to_bootstrap_build(self) -> None:
        with self.clock.patch():
            self.seed_two_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.deflate_live_generation_to_flat_layout()

            os.remove(self.export_dir / "zh" / "items" / "en-june-item.json")

            self.clock.advance(hours=1)
            with self.assertLogs("publish.orchestrator", level="WARNING") as logged:
                summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertTrue(any("does not match the DB plan" in m for m in logged.output), logged.output)
            self.assertEqual(support.read_pointer(self.export_dir)["generation"], "2026-07-01T01-00-00Z")

    def test_flat_stats_with_invalid_run_timestamp_falls_back_to_bootstrap_build(self) -> None:
        """The migrated generation id derives from the flat stats timestamp;
        an invalid one counts as verification failure, not a crash."""
        with self.clock.patch():
            self.seed_two_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.deflate_live_generation_to_flat_layout()

            stats_path = self.export_dir / "stats.json"
            stats = support.read_json(stats_path)
            stats["last_export_run_timestamp"] = "not-a-timestamp"
            stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

            self.clock.advance(hours=1)
            with self.assertLogs("publish.orchestrator", level="WARNING") as logged:
                summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertTrue(any("does not match the DB plan" in m for m in logged.output), logged.output)
            self.assertEqual(support.read_pointer(self.export_dir)["generation"], "2026-07-01T01-00-00Z")


class TestCorruptStateFailStop(PublishB1TestCase):
    """A corrupt current.json or live meta.json is a manual-intervention
    state: the run fails stop instead of silently rebuilding or ignoring it.
    Only a *missing* pointer triggers migration/bootstrap."""

    def test_corrupt_pointer_variants_fail_stop(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            valid_pointer = support.read_pointer(self.export_dir)
            generations_before = self.generation_names()

            variants = {
                "unparseable JSON": "{ not json",
                "missing field": {k: v for k, v in valid_pointer.items() if k != "content_fingerprint"},
                "invalid generation id": {**valid_pointer, "generation": "2026-07-01"},
                "path traversal generation id": {**valid_pointer, "generation": "../../outside"},
                "generation directory missing": {**valid_pointer, "generation": "2099-01-01T00-00-00Z"},
                "languages not a list": {**valid_pointer, "languages": "zh"},
                "empty languages list": {**valid_pointer, "languages": []},
                "malformed fingerprint": {**valid_pointer, "content_fingerprint": "deadbeef"},
                "malformed timestamp": {**valid_pointer, "export_completed_at": "yesterday"},
                "calendar-invalid timestamp": {
                    **valid_pointer, "export_completed_at": "2026-02-30T12:00:00Z",
                },
                "out-of-range hour": {
                    **valid_pointer, "last_successful_run_at": "2026-07-01T24:00:00Z",
                },
            }
            for name, payload in variants.items():
                with self.subTest(variant=name):
                    raw = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
                    (self.export_dir / "current.json").write_text(raw, encoding="utf-8")
                    with self.assertRaises(RuntimeError):
                        support.run_publish(self.config, self.db_path, self.export_dir)
                    # Nothing new was built and the live generation survived.
                    self.assertEqual(generations_before, self.generation_names())

            # Restoring the valid pointer lets the run converge again.
            (self.export_dir / "current.json").write_text(
                json.dumps(valid_pointer, indent=2), encoding="utf-8"
            )
            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(generations_before, self.generation_names())
            self.assertEqual(
                support.read_pointer(self.export_dir)["last_successful_run_at"],
                self.clock.now_iso,
            )

    def test_missing_or_corrupt_live_meta_json_fails_stop(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            generations_before = self.generation_names()
            meta_path = support.live_root(self.export_dir) / "meta.json"
            original_meta = meta_path.read_text(encoding="utf-8")

            meta_path.unlink()
            with self.assertRaises(RuntimeError) as ctx:
                support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("meta.json", str(ctx.exception))

            meta_path.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("meta.json", str(ctx.exception))

            # An empty aggregate hash table is corruption, not a zero-data
            # state: every legitimately built or migrated generation records
            # at least stats.json and the per-language aggregate files.
            meta = json.loads(original_meta)
            meta["aggregate_file_hashes"] = {}
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("meta.json", str(ctx.exception))

            self.assertEqual(generations_before, self.generation_names())


class TestRebuildSemantics(PublishB1TestCase):
    """rebuild always builds a complete new generation (forced stamp
    refresh); the following no-change incremental run must stay on it."""

    def test_rebuild_forces_new_generation_and_next_incremental_run_stays(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            t0 = self.clock.now_iso
            first = support.read_pointer(self.export_dir)
            first_root = support.live_root(self.export_dir)

            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
            t1 = self.clock.now_iso

            # The rebuild summary reports the full active published set.
            self.assertEqual(summary["published_count"], 4)

            rebuilt = support.read_pointer(self.export_dir)
            self.assertNotEqual(first["generation"], rebuilt["generation"])
            self.assertEqual(rebuilt["generation"], "2026-07-01T01-00-00Z")
            self.assertEqual(rebuilt["export_completed_at"], t1)
            self.assertEqual(rebuilt["last_successful_run_at"], t1)
            # Rebuild restamps every manifest to run_ts, so the planned
            # manifest bytes — and therefore the fingerprint — legitimately
            # change even though item content did not.
            self.assertNotEqual(rebuilt["content_fingerprint"], first["content_fingerprint"])

            # Aggregates are restamped; item payloads (DB-published content)
            # are byte-identical across the rebuild.
            self.assertEqual(support.read_stats(self.export_dir)["last_export_run_timestamp"], t1)
            for lang in ("zh", "en"):
                with self.subTest(language=lang):
                    for entry in support.read_manifest(self.export_dir, lang):
                        self.assertEqual(entry["updated_at"], t1)
            rebuilt_root = support.live_root(self.export_dir)
            item_rel = pathlib.Path("zh") / "items" / "en-june-item.json"
            self.assertEqual(
                (first_root / item_rel).read_bytes(),
                (rebuilt_root / item_rel).read_bytes(),
            )

            # The next no-change incremental run must not build again: the
            # rebuild's stamps converge via the new generation's meta.json.
            self.clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            settled = support.read_pointer(self.export_dir)
            self.assertEqual(settled["generation"], rebuilt["generation"])
            self.assertEqual(settled["last_successful_run_at"], self.clock.now_iso)
            self.assertEqual(support.read_stats(self.export_dir)["last_export_run_timestamp"], t1)


class TestArchiveStamping(PublishB1TestCase):
    """With a matching meta.json hash the planned manifest stamp is the
    recorded DB value verbatim — never the run's wall clock — so content
    that did not change keeps its original updated_at across generations."""

    def test_unchanged_archive_keeps_db_stamp_via_meta_hash_match(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "April Item", "2026-04-10T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            t0 = self.clock.now_iso
            first_root = support.live_root(self.export_dir)

            # Simulate pre-existing (e.g. pre-B1) metadata: move one month's
            # DB stamp without touching any content.
            conn = get_connection(self.db_path)
            conn.execute(
                "UPDATE publish_archive_metadata SET updated_at = ? WHERE language_code = 'zh' AND archive_month = '2026-04'",
                ("1999-01-01T00:00:00Z",),
            )
            conn.commit()
            conn.close()

            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["published_count"], 0)

            # The planned manifest changed (it now carries the DB stamp), so
            # a new generation is built — with the DB value verbatim.
            rebuilt_root = support.live_root(self.export_dir)
            self.assertNotEqual(first_root, rebuilt_root)
            zh_manifest = {e["archive_month"]: e for e in support.read_manifest(self.export_dir, "zh")}
            self.assertEqual(zh_manifest["2026-04"]["updated_at"], "1999-01-01T00:00:00Z")
            self.assertEqual(zh_manifest["2026-05"]["updated_at"], t0)
            en_manifest = {e["archive_month"]: e for e in support.read_manifest(self.export_dir, "en")}
            self.assertEqual(en_manifest["2026-04"]["updated_at"], t0)

            # The archive payload itself was not restamped or rewritten.
            archive_rel = pathlib.Path("zh") / "archives" / "archive_2026_04.json"
            self.assertEqual(
                (first_root / archive_rel).read_bytes(),
                (rebuilt_root / archive_rel).read_bytes(),
            )

            # And the state is settled: a further no-change run builds nothing.
            self.clock.advance(hours=1)
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(support.live_root(self.export_dir), rebuilt_root)


if __name__ == "__main__":
    unittest.main()
