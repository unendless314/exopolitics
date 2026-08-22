"""
Disk-backed temporary digest index backing hardlink reuse.

EXECUTION_POLICY Section 9 forbids emission-time memory proportional to item
count, so a run's per-artifact digest bookkeeping lives in a temporary SQLite
file at the export root instead of an in-memory dict:

- ``planned``: the planned digest of every artifact, appended in fixed
  artifact order during the fingerprint pass and consumed in the same order
  by the write pass (digest carry-over: a reused artifact is linked without
  re-serialization and without a database re-read).
- ``prior``: the live generation's ``file_hashes.jsonl`` stream, loaded
  record by record, for by-path reuse lookups during stamping and the write
  pass. Its PRIMARY KEY doubles as the duplicate-path corruption detector.

The index is builder-owned scratch state: a dotfile at the export root,
inert to readers (which enter only through ``current.json``), created after
the generation phase begins and discarded at run teardown. A crashed run's
complete owned SQLite set (main file plus ``-journal``/``-wal``/``-shm``
sidecars) is removed when the next run creates its index, so stale state can
never leak into a later run.
"""
import logging
import pathlib
import sqlite3
from typing import Iterable, Iterator, Optional, Tuple

logger = logging.getLogger("publish.digest_index")

DIGEST_INDEX_FILE_NAME = ".digest-index.tmp.sqlite"

# The owned SQLite file set: the main file plus every sidecar SQLite may
# create next to it, so startup recovery and teardown never leave halves of
# a crashed run's index behind.
_OWNED_SUFFIXES = ("", "-journal", "-wal", "-shm")


class DigestIndex:
    """Temporary per-run digest index backed by a SQLite spill file."""

    def __init__(self, export_dir: pathlib.Path) -> None:
        # A bootstrap run legitimately begins before the export root exists.
        export_dir.mkdir(parents=True, exist_ok=True)
        self._path = export_dir / DIGEST_INDEX_FILE_NAME
        for suffix in _OWNED_SUFFIXES:
            try:
                pathlib.Path(str(self._path) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            "CREATE TABLE planned ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path TEXT NOT NULL UNIQUE, "
            "digest TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE prior (path TEXT PRIMARY KEY, digest TEXT NOT NULL)"
        )

    def add_planned_batch(self, entries: Iterable[Tuple[str, str]]) -> None:
        """Append one fingerprint-pass batch of (path, digest) records."""
        batch = list(entries)
        if not batch:
            return
        self._conn.executemany(
            "INSERT INTO planned (path, digest) VALUES (?, ?)", batch
        )
        self._conn.commit()

    def add_prior(self, path: str, digest: str) -> None:
        self._conn.execute(
            "INSERT INTO prior (path, digest) VALUES (?, ?)", (path, digest)
        )

    def prior_digest_for(self, path: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT digest FROM prior WHERE path = ?", (path,)
        ).fetchone()
        return row[0] if row is not None else None

    _PLANNED_PAGE_SIZE = 1000

    def iter_planned(self) -> Iterator[Tuple[str, str]]:
        """(path, digest) for every planned artifact in fixed artifact order.

        Paged with short-lived cursors (LIMIT/OFFSET) instead of one held
        cursor: a consumer that aborts mid-iteration (for example a
        fail-stop build error) must not leave an active statement behind —
        on Windows an open cursor keeps the SQLite file locked even after
        the connection is closed, and teardown's unlink would silently fail.
        """
        offset = 0
        while True:
            rows = self._conn.execute(
                "SELECT path, digest FROM planned ORDER BY seq LIMIT ? OFFSET ?",
                (self._PLANNED_PAGE_SIZE, offset),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                yield row[0], row[1]
            offset += len(rows)

    def close(self) -> None:
        try:
            self._conn.commit()
        except sqlite3.Error:
            pass
        self._conn.close()

    def discard(self) -> None:
        """Close and remove the owned SQLite set. Best-effort teardown; never
        raises. The close must precede unlinking because Windows will not
        delete an open SQLite file."""
        try:
            self.close()
        except Exception:
            pass
        for suffix in _OWNED_SUFFIXES:
            try:
                pathlib.Path(str(self._path) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
