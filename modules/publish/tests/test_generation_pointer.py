"""
Generation + pointer contract tests
(known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md).

Covers the generation/pointer surface not pinned down by the rewritten
pre-refactor tests: bootstrap pointer/meta.json shape, generation id allocation
(including the same-second suffix), pointer write atomicity (sharing-
violation retry and fail-stop), the single-writer process lock, the held
generation-phase SQLite snapshot, retention
(keep-5, live-generation protection, warn-only deletion failures),
flat-residue bootstrap, fail-stop on a corrupt pointer or
live meta.json, rebuild semantics and hash-stream-driven archive
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

from modules.publish.src import generation, generation_store
from modules.publish.src.database import PublishRepository, run_migrations, get_connection
from modules.publish.src.digest_index import DIGEST_INDEX_FILE_NAME
from modules.publish.src.process_lock import ProcessLock
from modules.publish.tests import support

FINGERPRINT_RE = re.compile(r"^sha256-exportstate-v1:[0-9a-f]{64}$")


def _file_key(path: pathlib.Path) -> tuple:
    """(st_dev, st_ino) identity of a file: equal iff two paths share an inode."""
    st = os.stat(path)
    return (st.st_dev, st.st_ino)


def _make_dir_link(link_path: pathlib.Path, target_path: pathlib.Path) -> None:
    """Directory junction (Windows) or symlink, mirroring the pattern of
    test_coverage_loss.TestRetentionLinkSafety."""
    import subprocess

    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
            check=True, capture_output=True,
        )
    else:
        os.symlink(target_path, link_path, target_is_directory=True)


class PublishTestCase(unittest.TestCase):
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

    def assert_generation_hash_stream_matches(self, generation_root: pathlib.Path) -> dict:
        """meta.json must reference file_hashes.jsonl; the stream must cover
        every artifact of the generation (including item payloads, excluding
        builder-owned meta.json) with digests matching the actual on-disk
        bytes, paths unique, and stats.json as the final record."""
        meta = support.read_json(generation_root / "meta.json")
        self.assertEqual(meta.get("file_hashes"), "file_hashes.jsonl")
        records = support.read_hash_stream(generation_root)
        self.assertTrue(records, "the hash stream must record every artifact")
        paths = [record["path"] for record in records]
        self.assertEqual(len(paths), len(set(paths)), "stream paths must not repeat")
        on_disk = {
            str(p.relative_to(generation_root)).replace(os.sep, "/")
            for p in generation_root.rglob("*.json")
        }
        on_disk.discard("meta.json")
        self.assertEqual(set(paths), on_disk)
        self.assertEqual(paths[-1], "stats.json")
        for record in records:
            digest = hashlib.sha256((generation_root / record["path"]).read_bytes()).hexdigest()
            self.assertEqual(record["digest"], f"sha256:{digest}", record["path"])
        return meta


class TestBootstrapGeneration(PublishTestCase):
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
        meta = self.assert_generation_hash_stream_matches(generation_root)
        self.assertEqual(meta["generation"], expected_id)
        self.assertEqual(meta["created_at"], run_ts)
        self.assertEqual(meta["content_fingerprint"], pointer["content_fingerprint"])
        self.assertEqual(
            {record["path"] for record in support.read_hash_stream(generation_root)},
            {
                "stats.json",
                "zh/index.json",
                "zh/archives/index.json",
                "zh/archives/archive_2026_06.json",
                "zh/items/en-june-item.json",
                "en/index.json",
                "en/archives/index.json",
                "en/archives/archive_2026_06.json",
                "en/items/en-june-item.json",
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

        meta = self.assert_generation_hash_stream_matches(support.live_root(self.export_dir))
        # stats.json + per-language index and (empty) archives manifest.
        self.assertEqual(
            {record["path"] for record in support.read_hash_stream(support.live_root(self.export_dir))},
            {"stats.json", "zh/index.json", "zh/archives/index.json", "en/index.json", "en/archives/index.json"},
        )


class TestGenerationIdAllocation(PublishTestCase):
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


class TestPointerAtomicity(PublishTestCase):
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


class TestSingleWriterLock(PublishTestCase):
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


class TestGenerationPhaseSnapshot(PublishTestCase):
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


class TestRetention(PublishTestCase):
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


class TestFlatResidueBootstrap(PublishTestCase):
    """Flat-layout residue at the export root (a leftover pre-generation
    tree) is inert: with no pointer the run bootstraps the first complete
    generation from the DB and leaves the residue untouched."""

    def deflate_live_generation_to_flat_layout(self) -> None:
        """Turn the generation-based export root back into the flat layout:
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

    def test_flat_residue_without_pointer_is_ignored_and_bootstraps(self) -> None:
        """No pointer plus flat residue (stats.json and language directories)
        at the export root: the run bootstraps straight from the DB, the
        residue stays in place, and no 'does not match the DB plan' warning
        is emitted (there is no flat-tree verification anymore)."""
        with self.clock.patch():
            self.seed_two_items()
            support.run_publish(self.config, self.db_path, self.export_dir)
            self.deflate_live_generation_to_flat_layout()

            # DB runs ahead of the residue.
            support.seed_item(self.db_path, 3, "July Item", "2026-07-01T12:00:00Z")

            self.clock.advance(hours=1)
            with self.assertNoLogs("publish.orchestrator", level="WARNING"):
                summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")

            # Bootstrap build keyed by this run's timestamp, with all items.
            pointer = support.read_pointer(self.export_dir)
            self.assertEqual(pointer["generation"], "2026-07-01T01-00-00Z")
            self.assertEqual(pointer["export_completed_at"], self.clock.now_iso)
            self.assertEqual(support.read_item(self.export_dir, "zh", "en-july-item")["slug"], "en-july-item")

            # The residue is left in place, inert.
            self.assertTrue((self.export_dir / "stats.json").exists())
            self.assertTrue((self.export_dir / "zh").exists())


class TestCorruptStateFailStop(PublishTestCase):
    """A corrupt current.json or live meta.json is a manual-intervention
    state: the run fails stop instead of silently rebuilding or ignoring it.
    Only a *missing* pointer triggers bootstrap."""

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

            # A reference-less meta.json that fails the legacy witness checks
            # (here: an empty aggregate_file_hashes table) is corruption —
            # e.g. a meta.json whose file_hashes reference was lost —
            # not a legacy generation.
            meta = json.loads(original_meta)
            del meta["file_hashes"]
            meta["aggregate_file_hashes"] = {}
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("meta.json", str(ctx.exception))

            self.assertEqual(generations_before, self.generation_names())


class TestRebuildSemantics(PublishTestCase):
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


class TestArchiveStamping(PublishTestCase):
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

            # Simulate pre-existing (legacy-era) metadata: move one month's
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


class TestHashStreamFormat(PublishTestCase):
    """The per-generation hash stream: one record per artifact in fixed
    artifact order, digests of the actual written bytes."""

    def test_stream_covers_every_artifact_in_fixed_order_with_disk_matching_digests(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "Alpha May Item", "2026-05-10T12:00:00Z")
            support.seed_item(self.db_path, 3, "Beta May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)

        generation_root = support.live_root(self.export_dir)
        self.assert_generation_hash_stream_matches(generation_root)
        records = support.read_hash_stream(generation_root)
        expected = []
        for lang in ("zh", "en"):
            expected += [
                f"{lang}/index.json",
                f"{lang}/archives/index.json",
                f"{lang}/archives/archive_2026_05.json",
                f"{lang}/archives/archive_2026_06.json",
                f"{lang}/items/en-alpha-may-item.json",
                f"{lang}/items/en-beta-may-item.json",
                f"{lang}/items/en-june-item.json",
            ]
        expected.append("stats.json")
        self.assertEqual([record["path"] for record in records], expected)

    def test_stats_record_digest_matches_real_bytes_including_timestamp(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            first_root = support.live_root(self.export_dir)

            stats_bytes = (first_root / "stats.json").read_bytes()
            stats = json.loads(stats_bytes)
            self.assertIn("last_export_run_timestamp", stats)
            records = support.read_hash_stream(first_root)
            self.assertEqual(records[-1]["path"], "stats.json")
            self.assertEqual(
                records[-1]["digest"],
                f"sha256:{hashlib.sha256(stats_bytes).hexdigest()}",
            )
            # The dual-digest rule: the recorded digest covers the real
            # on-disk bytes (timestamp included), so it differs from the
            # excluded-timestamp variant used inside content_fingerprint.
            excluded = {k: v for k, v in stats.items() if k != "last_export_run_timestamp"}
            excluded_digest = hashlib.sha256(generation.serialize_json_bytes(excluded)).hexdigest()
            self.assertNotEqual(records[-1]["digest"], f"sha256:{excluded_digest}")

            # stats.json bytes advance with every build, so it is physically
            # written (never linked) whenever its bytes differ.
            self.clock.advance(hours=1)
            self.bump_content(1, "fp_stats", new_title="June Item Retitled")
            support.run_publish(self.config, self.db_path, self.export_dir)
            second_root = support.live_root(self.export_dir)
            self.assertNotEqual(
                _file_key(first_root / "stats.json"),
                _file_key(second_root / "stats.json"),
            )
            self.assertNotEqual(
                (first_root / "stats.json").read_bytes(),
                (second_root / "stats.json").read_bytes(),
            )
            second_stats_bytes = (second_root / "stats.json").read_bytes()
            second_records = support.read_hash_stream(second_root)
            self.assertEqual(
                second_records[-1]["digest"],
                f"sha256:{hashlib.sha256(second_stats_bytes).hexdigest()}",
            )


class TestDigestIndexLifecycle(PublishTestCase):
    """The run's temporary digest index: created even before the export root
    exists (bootstrap), discarded at teardown, and a crashed run's owned
    SQLite set is recovered when the next run creates its index."""

    def index_paths(self) -> list:
        base = self.export_dir / DIGEST_INDEX_FILE_NAME
        return [pathlib.Path(str(base) + suffix) for suffix in ("", "-journal", "-wal", "-shm")]

    def test_digest_index_creates_missing_export_root_for_bootstrap(self) -> None:
        self.assertFalse(self.export_dir.exists())
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
        self.assertEqual(summary["status"], "success")
        self.assertTrue(self.export_dir.is_dir())
        for path in self.index_paths():
            self.assertFalse(path.exists(), path)

    def test_digest_index_cleanup_removes_owned_sqlite_sidecars(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)

            # Simulate a crashed run: junk bytes in the main file (opening
            # it without recovery would fail with "file is not a database")
            # plus every sidecar SQLite may leave behind.
            for path in self.index_paths():
                path.write_bytes(b"stale junk from a crashed run")

            self.clock.advance(hours=1)
            self.bump_content(1, "fp_crash", new_title="June Item Retitled")
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(self.generation_names()[-1], "2026-07-01T01-00-00Z")
        for path in self.index_paths():
            self.assertFalse(path.exists(), path)


class TestHashStreamCorruption(PublishTestCase):
    """The hash stream is validated as it is read (plan acceptance bullet
    4): a missing or empty stream, malformed records, illegal or duplicate
    paths, an unexpected meta.json reference value and valid-prefix
    truncation all fail stop. A mid-stream digest edit is the
    safe-degradation half: it must not fail, only forfeit reuse of that
    entry."""

    def setUp(self) -> None:
        super().setUp()
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
        self.live = support.live_root(self.export_dir)
        self.stream_path = self.live / generation_store.HASH_STREAM_NAME
        self.original_lines = self.stream_path.read_text(encoding="utf-8").splitlines()
        self.records = support.read_hash_stream(self.live)
        self.generations_before = self.generation_names()

    def rewrite_stream(self, records: list) -> None:
        self.stream_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def assert_fail_stop(self) -> None:
        with self.assertRaises(RuntimeError):
            support.run_publish(self.config, self.db_path, self.export_dir)
        # Nothing new was built and the live pointer survived.
        self.assertEqual(self.generation_names(), self.generations_before)
        self.assertEqual(support.read_pointer(self.export_dir)["generation"], self.live.name)

    def test_referenced_stream_missing_fails_stop(self) -> None:
        self.stream_path.unlink()
        self.assert_fail_stop()

    def test_referenced_stream_empty_fails_stop(self) -> None:
        self.stream_path.write_bytes(b"")
        self.assert_fail_stop()

    def test_malformed_stream_record_fails_stop(self) -> None:
        variants = {
            "non-JSON line": ["{ not json"] + self.original_lines[1:],
            "missing path": [json.dumps({"digest": self.records[0]["digest"]})] + self.original_lines[1:],
            "missing digest": [json.dumps({"path": self.records[0]["path"]})] + self.original_lines[1:],
            "bad digest format": [
                json.dumps({"path": self.records[0]["path"], "digest": "deadbeef"})
            ] + self.original_lines[1:],
            "non-object record": ["[1, 2]"] + self.original_lines[1:],
        }
        for name, lines in variants.items():
            with self.subTest(variant=name):
                self.stream_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.assert_fail_stop()

    def test_illegal_or_duplicate_stream_path_fails_stop(self) -> None:
        bad_paths = {
            "parent segment": "zh/../stats.json",
            "leading slash": "/zh/index.json",
            "backslash": "zh\\index.json",
            "empty segment": "zh//index.json",
            "dot segment": "zh/./index.json",
        }
        for name, bad_path in bad_paths.items():
            with self.subTest(variant=name):
                self.rewrite_stream([dict(self.records[0], path=bad_path)] + self.records[1:])
                self.assert_fail_stop()
        with self.subTest(variant="duplicate path"):
            self.rewrite_stream([self.records[0]] + self.records)
            self.assert_fail_stop()

    def test_valid_prefix_truncation_fails_stop(self) -> None:
        # Drop the final line: every remaining record is well-formed, but
        # the final record is no longer stats.json.
        self.assertEqual(self.records[-1]["path"], "stats.json")
        self.rewrite_stream(self.records[:-1])
        self.assert_fail_stop()

    def test_meta_reference_with_unexpected_value_fails_stop(self) -> None:
        meta_path = self.live / "meta.json"
        meta = support.read_json(meta_path)
        variants = {
            "different file name": "hashes.jsonl",
            "relative path injection": "../other/file_hashes.jsonl",
            "absolute path": "/tmp/file_hashes.jsonl",
        }
        for name, value in variants.items():
            with self.subTest(variant=name):
                meta_path.write_text(
                    json.dumps({**meta, "file_hashes": value}), encoding="utf-8"
                )
                self.assert_fail_stop()

    def test_mid_stream_digest_edit_degrades_to_physical_write(self) -> None:
        with self.clock.patch():
            # Forge a non-matching digest for one unchanged item artifact.
            tampered = [dict(record) for record in self.records]
            target = next(r for r in tampered if r["path"] == "zh/items/en-june-item.json")
            target["digest"] = "sha256:" + hashlib.sha256(b"forged").hexdigest()
            self.rewrite_stream(tampered)

            self.clock.advance(hours=1)
            self.bump_content(2, "fp_tamper", new_title="May Item Retitled")
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            second_root = support.live_root(self.export_dir)

        self.assertNotEqual(second_root, self.live)
        # The tampered entry forfeits reuse: physically rewritten, with the
        # correct planned bytes (identical to the prior content).
        first_file = self.live / "zh" / "items" / "en-june-item.json"
        second_file = second_root / "zh" / "items" / "en-june-item.json"
        self.assertNotEqual(_file_key(first_file), _file_key(second_file))
        self.assertEqual(first_file.read_bytes(), second_file.read_bytes())
        # An unchanged artifact with an intact digest is still reused.
        self.assertEqual(
            _file_key(self.live / "en" / "items" / "en-june-item.json"),
            _file_key(second_root / "en" / "items" / "en-june-item.json"),
        )
        self.assert_generation_hash_stream_matches(second_root)


class TestLegacyTransition(PublishTestCase):
    """Legacy transition: a live generation whose meta.json positively
    matches the legacy aggregate shape carries no reuse information —
    tolerated, its hashes never consulted; anything else without a
    file_hashes reference fails stop."""

    def setUp(self) -> None:
        super().setUp()
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
        self.live = support.live_root(self.export_dir)

    def make_live_generation_legacy(self) -> dict:
        """Rewrite the live generation's metadata to the legacy aggregate
        shape: genuinely matching digests in an aggregate_file_hashes table
        inside meta.json, no file_hashes reference, no stream file."""
        meta = support.read_json(self.live / "meta.json")
        aggregate_hashes = {}
        for path in sorted(self.live.rglob("*.json")):
            rel = str(path.relative_to(self.live)).replace(os.sep, "/")
            if rel == "meta.json" or "/items/" in rel:
                continue
            aggregate_hashes[rel] = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        legacy_meta = {
            "generation": meta["generation"],
            "created_at": meta["created_at"],
            "content_fingerprint": meta["content_fingerprint"],
            "aggregate_file_hashes": aggregate_hashes,
        }
        (self.live / "meta.json").write_text(json.dumps(legacy_meta, indent=2), encoding="utf-8")
        (self.live / generation_store.HASH_STREAM_NAME).unlink()
        return legacy_meta

    def test_legacy_live_generation_no_change_run_neither_fails_nor_builds(self) -> None:
        self.make_live_generation_legacy()
        generations_before = self.generation_names()
        with self.clock.patch():
            self.clock.advance(hours=1)
            with self.assertLogs("publish.generation_store", level="INFO") as logged:
                summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            self.assertTrue(any("legacy pre-stream" in m for m in logged.output), logged.output)
        # No failure and no spurious build: the byte-compare stamping
        # fallback keeps the planned fingerprint equal to the pointer's, so
        # only the freshness signal advances.
        self.assertEqual(self.generation_names(), generations_before)
        pointer = support.read_pointer(self.export_dir)
        self.assertEqual(pointer["generation"], self.live.name)
        self.assertEqual(pointer["last_successful_run_at"], "2026-07-01T01:00:00Z")

    def test_legacy_live_generation_first_content_change_writes_everything_and_establishes_stream(self) -> None:
        self.make_live_generation_legacy()
        with self.clock.patch():
            self.clock.advance(hours=1)
            self.bump_content(1, "fp_legacy", new_title="June Item Retitled")
            with self.assertLogs("publish.generation_store", level="INFO"):
                summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            second_root = support.live_root(self.export_dir)
            self.assertNotEqual(second_root.name, self.live.name)

            # Full physical write: no artifact is linked from the legacy
            # generation, not even unchanged ones.
            for path in second_root.rglob("*.json"):
                if path.name == "meta.json":
                    continue
                prior_file = self.live / path.relative_to(second_root)
                if prior_file.is_file():
                    self.assertNotEqual(_file_key(path), _file_key(prior_file), path.name)
            self.assert_generation_hash_stream_matches(second_root)

            # From that generation on, normal hardlink reuse applies: a
            # further change to item 2 links item 1's untouched artifacts.
            self.clock.advance(hours=1)
            self.bump_content(2, "fp_legacy_2", new_title="May Item Retitled")
            support.run_publish(self.config, self.db_path, self.export_dir)
            third_root = support.live_root(self.export_dir)
            for lang in ("zh", "en"):
                self.assertEqual(
                    _file_key(second_root / lang / "items" / "en-june-item.json"),
                    _file_key(third_root / lang / "items" / "en-june-item.json"),
                )
            self.assert_generation_hash_stream_matches(third_root)

    def test_legacy_witness_hashes_are_never_used_for_reuse(self) -> None:
        """A legacy-shaped meta.json carrying genuinely matching digests still
        gets zero links: the witness table is a format witness, never a
        hash source."""
        legacy_meta = self.make_live_generation_legacy()
        # Sanity: the witness digest for the untouched May archive genuinely
        # matches its on-disk bytes.
        may_rel = pathlib.Path("zh") / "archives" / "archive_2026_05.json"
        self.assertEqual(
            legacy_meta["aggregate_file_hashes"]["zh/archives/archive_2026_05.json"],
            f"sha256:{hashlib.sha256((self.live / may_rel).read_bytes()).hexdigest()}",
        )
        with self.clock.patch():
            self.clock.advance(hours=1)
            self.bump_content(1, "fp_witness", new_title="June Item Retitled")
            support.run_publish(self.config, self.db_path, self.export_dir)
            second_root = support.live_root(self.export_dir)

        # The May archive is unchanged content with a matching witness
        # digest — and is still physically written, not linked.
        self.assertEqual(
            (self.live / may_rel).read_bytes(),
            (second_root / may_rel).read_bytes(),
        )
        self.assertNotEqual(_file_key(self.live / may_rel), _file_key(second_root / may_rel))

    def test_referenceless_meta_failing_witness_fails_stop(self) -> None:
        legacy_meta = self.make_live_generation_legacy()
        variants = {
            "bad generation id": {**legacy_meta, "generation": "../../outside"},
            "calendar-invalid created_at": {**legacy_meta, "created_at": "2026-02-30T00:00:00Z"},
            "bad fingerprint": {**legacy_meta, "content_fingerprint": "deadbeef"},
            "empty aggregate table": {**legacy_meta, "aggregate_file_hashes": {}},
            "illegal path key": {
                **legacy_meta,
                "aggregate_file_hashes": {"../escape.json": "sha256:" + "0" * 64},
            },
            "bad digest value": {
                **legacy_meta,
                "aggregate_file_hashes": {"stats.json": "sha256:not-hex"},
            },
        }
        generations_before = self.generation_names()
        for name, payload in variants.items():
            with self.subTest(variant=name):
                (self.live / "meta.json").write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    support.run_publish(self.config, self.db_path, self.export_dir)
                self.assertEqual(self.generation_names(), generations_before)

    def test_null_file_hashes_reference_fails_stop(self) -> None:
        """A present-but-null file_hashes field is corruption, not legacy:
        a genuine legacy meta.json never carries the field, so even a valid
        aggregate table beside it must not route to the witness path."""
        legacy_meta = self.make_live_generation_legacy()
        variants = {
            # The review scenario: a damaged newer meta.json that still
            # carries a valid-looking aggregate table.
            "null beside valid aggregate table": {**legacy_meta, "file_hashes": None},
            # A stripped meta.json holding only the null reference.
            "null reference alone": {
                k: legacy_meta[k] for k in ("generation", "created_at", "content_fingerprint")
            } | {"file_hashes": None},
        }
        generations_before = self.generation_names()
        for name, payload in variants.items():
            with self.subTest(variant=name):
                (self.live / "meta.json").write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(RuntimeError) as ctx:
                    support.run_publish(self.config, self.db_path, self.export_dir)
                self.assertIn("file_hashes", str(ctx.exception))
                self.assertEqual(self.generation_names(), generations_before)


class TestHardlinkReuse(PublishTestCase):
    """Unchanged artifacts are hardlinked from the trusted prior generation;
    changed ones are physically written (plan acceptance bullets 1 and 5).
    Inode comparison covers NTFS locally; Linux behavior is verified on the
    target VPS before rollout."""

    def test_unchanged_artifacts_are_hardlinked_and_changed_ones_rewritten(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            first_root = support.live_root(self.export_dir)

            self.clock.advance(hours=1)
            self.bump_content(1, "fp_link", new_title="June Item Retitled")
            support.run_publish(self.config, self.db_path, self.export_dir)
            second_root = support.live_root(self.export_dir)

        self.assertNotEqual(first_root, second_root)
        linked = [
            "zh/items/en-may-item.json",
            "en/items/en-may-item.json",
            "zh/archives/archive_2026_05.json",
            "en/archives/archive_2026_05.json",
        ]
        rewritten = [
            "zh/items/en-june-item.json",
            "en/items/en-june-item.json",
            "zh/index.json",
            "en/index.json",
            "zh/archives/index.json",
            "en/archives/index.json",
            "zh/archives/archive_2026_06.json",
            "en/archives/archive_2026_06.json",
            "stats.json",
        ]
        for rel in linked:
            with self.subTest(linked=rel):
                self.assertEqual(_file_key(first_root / rel), _file_key(second_root / rel))
        for rel in rewritten:
            with self.subTest(rewritten=rel):
                self.assertNotEqual(_file_key(first_root / rel), _file_key(second_root / rel))
        self.assert_generation_hash_stream_matches(second_root)

    def test_reused_items_skip_second_db_read_and_serialization(self) -> None:
        """Digest carry-over: the full item stream runs exactly once per run
        (the fingerprint pass); the write pass re-reads only changed items,
        one fetch each."""
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)

            self.clock.advance(hours=1)
            self.bump_content(1, "fp_spy", new_title="June Item Retitled")

            stream_calls = []
            real_iter = generation._iter_item_payloads

            def counting_iter(repo, config, language_code):
                stream_calls.append(language_code)
                yield from real_iter(repo, config, language_code)

            real_fetch = PublishRepository.fetch_published_payload_by_slug
            fetch_calls = []

            def counting_fetch(repository, language_code, slug):
                fetch_calls.append((language_code, slug))
                return real_fetch(repository, language_code, slug)

            with patch.object(generation, "_iter_item_payloads", counting_iter), patch.object(
                PublishRepository, "fetch_published_payload_by_slug", counting_fetch
            ):
                summary = support.run_publish(self.config, self.db_path, self.export_dir)

            self.assertEqual(summary["status"], "success")
            self.assertEqual(stream_calls, ["zh", "en"])
            self.assertEqual(
                sorted(fetch_calls),
                [("en", "en-june-item"), ("zh", "en-june-item")],
            )

    def test_reuse_works_with_item_count_several_times_batch_size(self) -> None:
        config = support.make_config(export_dir=self.export_dir, batch_size=5, latest_limit=50)
        months = ["2026-03", "2026-04", "2026-05", "2026-06"]
        with self.clock.patch():
            for index in range(1, 24):
                support.seed_item(
                    self.db_path, index, f"Batch Item {index:02d}", f"{months[index % 4]}-15T12:00:00Z"
                )
            support.run_publish(config, self.db_path, self.export_dir)
            first_root = support.live_root(self.export_dir)

            self.clock.advance(hours=1)
            self.bump_content(7, "fp_batch", new_title="Batch Item 07 Retitled")
            support.run_publish(config, self.db_path, self.export_dir)
            second_root = support.live_root(self.export_dir)

        for index in range(1, 24):
            slug = f"en-batch-item-{index:02d}"
            for lang in ("zh", "en"):
                rel = f"{lang}/items/{slug}.json"
                with self.subTest(artifact=rel):
                    if index == 7:
                        self.assertNotEqual(_file_key(first_root / rel), _file_key(second_root / rel))
                    else:
                        self.assertEqual(_file_key(first_root / rel), _file_key(second_root / rel))
        self.assert_generation_hash_stream_matches(second_root)


class TestLinkSafety(PublishTestCase):
    """Link-time safety rules (plan acceptance bullets 7, 8, 9): link
    failures fall back to a physical write; a post-link verification
    mismatch removes the destination and fails stop; symlink, junction or
    reparse sources — directly or through a parent directory — are never
    linked."""

    def seed_items_and_first_generation(self) -> pathlib.Path:
        support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
        support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
        support.run_publish(self.config, self.db_path, self.export_dir)
        return support.live_root(self.export_dir)

    def test_link_failure_falls_back_to_physical_write(self) -> None:
        with self.clock.patch():
            first_root = self.seed_items_and_first_generation()

            self.clock.advance(hours=1)
            self.bump_content(1, "fp_exdev", new_title="June Item Retitled")
            # Simulated EXDEV/policy failure: no real cross-device setup
            # exists on the development machine.
            with patch(
                "modules.publish.src.generation_store.os.link",
                side_effect=OSError("simulated cross-device link failure"),
            ):
                summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            second_root = support.live_root(self.export_dir)

        # Every artifact was physically written; output stays byte-correct.
        for path in second_root.rglob("*.json"):
            if path.name == "meta.json":
                continue
            prior = first_root / path.relative_to(second_root)
            if prior.is_file():
                self.assertNotEqual(_file_key(path), _file_key(prior), path.name)
        rel = "zh/items/en-may-item.json"
        self.assertEqual((first_root / rel).read_bytes(), (second_root / rel).read_bytes())
        self.assert_generation_hash_stream_matches(second_root)

    def test_post_link_verification_mismatch_removes_destination_and_fails_stop(self) -> None:
        with self.clock.patch():
            first_root = self.seed_items_and_first_generation()
            pointer_before = support.read_pointer(self.export_dir)

            self.clock.advance(hours=1)
            self.bump_content(1, "fp_mismatch", new_title="June Item Retitled")

            def copy_instead_of_link(source, destination):
                shutil.copyfile(source, destination)

            with patch(
                "modules.publish.src.generation_store.os.link",
                side_effect=copy_instead_of_link,
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertIn("Post-link verification failed", str(ctx.exception))

        # The live pointer and generation are untouched; staging and the
        # digest index are cleaned up by teardown.
        self.assertEqual(support.read_pointer(self.export_dir), pointer_before)
        self.assertEqual(self.generation_names(), [first_root.name])
        self.assertFalse((self.export_dir / ".staging").exists())
        base = self.export_dir / DIGEST_INDEX_FILE_NAME
        for suffix in ("", "-journal", "-wal", "-shm"):
            self.assertFalse(pathlib.Path(str(base) + suffix).exists(), suffix)

    def test_non_regular_link_source_falls_back_to_physical_write(self) -> None:
        with self.clock.patch():
            first_root = self.seed_items_and_first_generation()

            victim = first_root / "zh" / "items" / "en-june-item.json"
            original_bytes = victim.read_bytes()
            outside = pathlib.Path(self.temp_dir.name) / "outside_payload.json"
            outside.write_bytes(b'{"smuggled": true}')
            victim.unlink()
            try:
                os.symlink(outside, victim)
            except OSError as exc:
                self.skipTest(f"cannot create a file symlink on this platform: {exc}")

            self.clock.advance(hours=1)
            self.bump_content(2, "fp_symlink", new_title="May Item Retitled")
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            second_root = support.live_root(self.export_dir)

        # The symlink source fails validation: the artifact is physically
        # written from the plan (never linked, never read through the link).
        second_file = second_root / "zh" / "items" / "en-june-item.json"
        self.assertFalse(os.path.islink(second_file))
        self.assertEqual(second_file.read_bytes(), original_bytes)
        self.assert_generation_hash_stream_matches(second_root)

    def test_nested_reparse_link_source_falls_back_to_physical_write(self) -> None:
        """A regular artifact reached through a symlink/junction parent must
        fail source-containment validation and never be linked."""
        with self.clock.patch():
            first_root = self.seed_items_and_first_generation()

            items_dir = first_root / "zh" / "items"
            june_bytes = (items_dir / "en-june-item.json").read_bytes()
            real_dir = first_root / "zh" / "items_real"
            os.rename(items_dir, real_dir)
            outside = pathlib.Path(self.temp_dir.name) / "outside_items"
            outside.mkdir()
            (outside / "en-june-item.json").write_bytes(b'{"smuggled": true}')
            (outside / "en-may-item.json").write_bytes((real_dir / "en-may-item.json").read_bytes())
            try:
                _make_dir_link(items_dir, outside)
            except Exception as exc:
                self.skipTest(f"cannot create a directory link on this platform: {exc}")

            self.clock.advance(hours=1)
            self.bump_content(2, "fp_nested", new_title="May Item Retitled")
            summary = support.run_publish(self.config, self.db_path, self.export_dir)
            self.assertEqual(summary["status"], "success")
            second_root = support.live_root(self.export_dir)

        # The June artifact (unchanged, digest matching) resolves through
        # the junction to a path outside the trusted prior generation, so it
        # is physically written from the plan — with the planned bytes, not
        # the smuggled ones, and never sharing the outside file's inode.
        second_file = second_root / "zh" / "items" / "en-june-item.json"
        self.assertEqual(second_file.read_bytes(), june_bytes)
        self.assertNotEqual(_file_key(second_file), _file_key(outside / "en-june-item.json"))
        self.assert_generation_hash_stream_matches(second_root)


class TestRebuildAndRetentionUnderReuse(PublishTestCase):
    """rebuild and retention under hardlink reuse (plan acceptance bullets
    10, 11, 12): rebuild is a full physical rewrite even when hashes match;
    linked files are never modified in place; retention unlinks retired
    generations without breaking artifacts shared with retained ones."""

    def test_rebuild_physically_rewrites_even_when_hashes_match(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            first_root = support.live_root(self.export_dir)

            self.clock.advance(hours=1)
            summary = support.run_publish(self.config, self.db_path, self.export_dir, rebuild=True)
            self.assertEqual(summary["status"], "success")
            second_root = support.live_root(self.export_dir)

        self.assertNotEqual(first_root, second_root)
        for path in second_root.rglob("*.json"):
            if path.name == "meta.json":
                continue
            self.assertNotEqual(
                _file_key(path),
                _file_key(first_root / path.relative_to(second_root)),
                path.name,
            )
        # Item payloads are DB-driven and byte-identical across the rebuild.
        rel = "zh/items/en-june-item.json"
        self.assertEqual((first_root / rel).read_bytes(), (second_root / rel).read_bytes())
        self.assert_generation_hash_stream_matches(second_root)

    def test_linked_files_are_never_modified_in_place(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            first_root = support.live_root(self.export_dir)
            snapshot = {
                p.relative_to(first_root): (os.stat(p).st_ino, p.read_bytes())
                for p in first_root.rglob("*.json")
            }

            self.clock.advance(hours=1)
            self.bump_content(1, "fp_immutable_1", new_title="June Item Retitled")
            support.run_publish(self.config, self.db_path, self.export_dir)
            second_root = support.live_root(self.export_dir)

            self.clock.advance(hours=1)
            self.bump_content(2, "fp_immutable_2", new_title="May Item Retitled")
            support.run_publish(self.config, self.db_path, self.export_dir)

        # The first generation is bit-identical after two subsequent builds,
        # including files that were hardlinked into later generations.
        for rel, (ino, data) in snapshot.items():
            path = first_root / rel
            self.assertEqual(os.stat(path).st_ino, ino, str(rel))
            self.assertEqual(path.read_bytes(), data, str(rel))
        may_rel = pathlib.Path("zh") / "items" / "en-may-item.json"
        self.assertEqual(_file_key(first_root / may_rel), _file_key(second_root / may_rel))

    def test_retention_unlinks_shared_inodes_without_breaking_retained_generations(self) -> None:
        with self.clock.patch():
            support.seed_item(self.db_path, 1, "June Item", "2026-06-15T12:00:00Z")
            support.seed_item(self.db_path, 2, "May Item", "2026-05-15T12:00:00Z")
            support.run_publish(self.config, self.db_path, self.export_dir)
            for index in range(1, 7):
                self.clock.advance(hours=1)
                self.bump_content(1, f"fp_ret_{index}", new_title=f"June Item Retitled {index}")
                support.run_publish(self.config, self.db_path, self.export_dir)

        # Seven builds with keep=5: the two oldest generations are retired.
        names = self.generation_names()
        self.assertEqual(len(names), 5)
        self.assertEqual(support.read_pointer(self.export_dir)["generation"], names[-1])

        # Item 2 never changed: one inode shared by every surviving
        # generation. Retiring the oldest two removed their links without
        # touching the shared bytes.
        live = support.live_root(self.export_dir)
        may_rel = "zh/items/en-may-item.json"
        expected_bytes = (live / may_rel).read_bytes()
        for name in names:
            path = self.export_dir / "generations" / name / may_rel
            self.assertEqual(path.read_bytes(), expected_bytes, name)
            self.assertEqual(_file_key(path), _file_key(live / may_rel), name)
        nlink = os.stat(live / may_rel).st_nlink
        if nlink > 1:
            # Filesystems that report link counts (NTFS, ext4): one link per
            # surviving generation, the retired links gone.
            self.assertEqual(nlink, len(names))


if __name__ == "__main__":
    unittest.main()
