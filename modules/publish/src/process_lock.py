"""
Single-writer process lock for the publish runner.

Introduced as part of Phase B1 of
known_issues/PUBLISH_EXPORT_GENERATION_POINTER_REFACTOR_PLAN.md: the whole run
(staging, DB state, pointer switch) holds this lock so two publish runs can
never operate concurrently. The implementation mirrors the curate module's
ProcessLock precedent (modules/curate/src/orchestrator.py) with non-blocking
acquire. Release only unlocks and closes the handle; the lock file itself is
deliberately left in place: unlinking it would reopen the classic POSIX
inode-reuse race (a process that already locked the old inode and a process
locking a freshly created inode would both believe they hold the lock).
"""
import os
import pathlib


class ProcessLock:
    def __init__(self, lock_path: pathlib.Path):
        self.lock_path = lock_path
        self.fp = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            try:
                self.fp = open(self.lock_path, 'w')
                import msvcrt
                msvcrt.locking(self.fp.fileno(), msvcrt.LK_NBLCK, 1)
            except (IOError, OSError, ImportError) as e:
                if self.fp:
                    self.fp.close()
                raise RuntimeError(f"Could not acquire lock on {self.lock_path}. Another process is running. ({e})")
        else:
            try:
                self.fp = open(self.lock_path, 'w')
                import fcntl
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError, ImportError) as e:
                if self.fp:
                    self.fp.close()
                raise RuntimeError(f"Could not acquire lock on {self.lock_path}. Another process is running. ({e})")

    def release(self) -> None:
        if self.fp:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.fp.seek(0)
                    msvcrt.locking(self.fp.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.fp.close()
            # The lock file is NOT removed: deleting the path after unlocking
            # lets a third process create and lock a new inode while another
            # process still holds the lock on the old (now unlinked) one,
            # breaking mutual exclusion. A stale lock file is harmless — the
            # lock state lives on the inode, not the path.
