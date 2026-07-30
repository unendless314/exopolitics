import os
import pathlib
import subprocess
import sys
import unittest

from modules.curate.src.orchestrator import ProcessLock
from modules.curate.tests.support import make_temp_workspace

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# Minimal child script: imports ProcessLock directly (never the CLI), tries to
# acquire the lock at the path passed as argv[1], prints a marker on success.
CHILD_SCRIPT = (
    "import pathlib, sys;"
    "from modules.curate.src.orchestrator import ProcessLock;"
    "lock = ProcessLock(pathlib.Path(sys.argv[1]));"
    "lock.acquire();"
    "print('ACQUIRED', flush=True);"
    "lock.release()"
)


def _run_lock_child(lock_path: pathlib.Path):
    """Runs the lock-acquire child process with a hard timeout and guaranteed
    termination so the test suite can never hang on a stuck child."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD_SCRIPT, str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    return proc.returncode, stdout, stderr


class TestProcessLock(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = make_temp_workspace(self)
        # Lock path lives inside the temporary workspace.
        self.lock_path = self.workspace / "data" / "curate_runner.lock"

    def test_cross_process_contention_and_reacquire(self):
        lock = ProcessLock(self.lock_path)
        lock.acquire()
        try:
            # While the parent holds the lock, the child must be refused.
            code, _out, err = _run_lock_child(self.lock_path)
            self.assertNotEqual(code, 0)
            self.assertIn("RuntimeError", err)
            self.assertIn("Could not acquire lock", err)
        finally:
            lock.release()

        # After release, a fresh child can acquire the same lock path.
        code, out, err = _run_lock_child(self.lock_path)
        self.assertEqual(code, 0, msg=f"child failed unexpectedly: {err}")
        self.assertIn("ACQUIRED", out)


if __name__ == "__main__":
    unittest.main()
