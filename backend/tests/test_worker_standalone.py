"""The worker imports suitability without the API: its poll must work in a fresh process."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


class WorkerStandaloneTests(unittest.TestCase):
    def run_py(self, code, env):
        return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)

    def test_suitability_poll_in_a_process_that_never_imported_the_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "DATABASE_URL": "sqlite:///" + str(Path(tmp) / "w.sqlite3"),
                   "NIT_JWT_SECRET": "test-only-signing-key-12345678901234567890", "NIT_STORAGE_DIR": tmp}
            setup = self.run_py(
                "from learningx.api import app\nfrom learningx.storage import init_db, SessionLocal\ninit_db()\n"
                "from learningx.suitability import SuitabilityCheck\n"
                "with SessionLocal() as db:\n"
                "    db.add(SuitabilityCheck(session_question_id='q1', revision_id='r1', status='pending', mode='model',\n"
                "        attempt_id='a1', policy_version='suitability-v1'))\n"
                "    db.commit()", env)
            self.assertEqual(setup.returncode, 0, setup.stderr)
            # Same imports as learningx.northflank_worker's suitability thread, in a fresh interpreter.
            poll = self.run_py("from learningx.suitability import poll_once\nprint(poll_once())", env)
            self.assertEqual(poll.returncode, 0, poll.stderr)
            # The check is claimed (a write), then dropped as stale: its question no longer exists.
            self.assertEqual(poll.stdout.strip(), "stale")



if __name__ == "__main__":
    unittest.main()
