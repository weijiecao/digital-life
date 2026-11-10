import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestCrashUnit(unittest.TestCase):
    def test_process_kill_before_commit_leaves_no_effect(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = str(Path(d) / "v15.sqlite3")
            store = V15Store(db_path)
            source = SourceEnvelope("s", 99, "sha", "t", "seed")
            event_id = store.create_event_from_source("s", source)
            store.close()
            code = """
import os, sys
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
store = V15Store(sys.argv[1])
source = SourceEnvelope('s', 1, 'sha', 't', 'hello')
with store.unit_of_work():
    store.attach_source('s', source, sys.argv[2], 'crash')
    store.record_receipt('s', 1, 'ATTACHED', source.source_hash)
    os._exit(9)
"""
            proc = subprocess.run([sys.executable, "-c", code, db_path, event_id],
                                  cwd=str(Path(__file__).resolve().parents[1]),
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 9, proc.stderr)
            reopened = V15Store(db_path)
            try:
                self.assertEqual(reopened.events_for_source("s", 1), [])
                self.assertEqual(reopened.db.execute(
                    "SELECT COUNT(*) FROM source_receipt").fetchone()[0], 0)
                self.assertEqual(reopened.contiguous_frontier("s"), 0)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
