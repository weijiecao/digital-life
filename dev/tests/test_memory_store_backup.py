import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestBackup(unittest.TestCase):
    def test_backup_contains_committed_data(self):
        with tempfile.TemporaryDirectory() as d:
            source = V15Store(str(Path(d) / "source.sqlite3"))
            engine = MemoryEngine(source)
            receipt = engine.ingest(
                "s", SourceEnvelope("s", 1, "sha", "t", "备份测试"))
            target = str(Path(d) / "backup.sqlite3")
            self.assertEqual(source.backup_to(target), "ok")
            backup = V15Store(target)
            try:
                self.assertEqual(backup.events_for_source("s", 1), [receipt.event_id])
                self.assertEqual(backup.integrity_check(), "ok")
            finally:
                backup.close()
                source.close()

    def test_backup_is_consistent_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            source = V15Store(str(Path(d) / "source.sqlite3"))
            source.create_event_from_source("s", SourceEnvelope("s", 1, "sha", "t", "a"))
            target = str(Path(d) / "backup.sqlite3")
            source.backup_to(target)
            source.create_event_from_source("s", SourceEnvelope("s", 2, "sha", "t", "b"))
            backup = V15Store(target)
            try:
                self.assertEqual(backup.db.execute(
                    "SELECT COUNT(*) FROM event_candidate").fetchone()[0], 1)
            finally:
                backup.close()
                source.close()


    def test_restore_reapplies_tombstones(self):
        with tempfile.TemporaryDirectory() as d:
            source = V15Store(str(Path(d) / "source.sqlite3"))
            engine = MemoryEngine(source)
            receipt = engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "要删除"))
            backup_path = str(Path(d) / "before-delete.sqlite3")
            source.backup_to(backup_path)
            engine.delete_source("s", 1)
            backup = V15Store(backup_path)
            try:
                self.assertEqual(backup.effective_events_for_source("s", 1), [receipt.event_id])
                self.assertEqual(source.apply_tombstones(backup), 1)
                self.assertEqual(backup.effective_events_for_source("s", 1), [])
            finally:
                backup.close()
                source.close()


if __name__ == "__main__":
    unittest.main()
