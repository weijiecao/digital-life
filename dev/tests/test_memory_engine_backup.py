import tempfile
import unittest
from pathlib import Path

from memory_engine.backup import BackupScheduler
from v1_5_consolidator.store import V15Store


class TestBackupScheduler(unittest.TestCase):
    def test_interval_and_retention(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            scheduler = BackupScheduler(store, Path(d) / "backups",
                                        interval_seconds=10, keep=2)
            first = scheduler.maybe_backup(now=1000)
            self.assertIsNotNone(first)
            self.assertIsNone(scheduler.maybe_backup(now=1001))
            scheduler.maybe_backup(now=1011)
            scheduler.maybe_backup(now=1021)
            self.assertEqual(len(scheduler.backups()), 2)
            restarted = BackupScheduler(store, Path(d) / "backups",
                                        interval_seconds=10, keep=2)
            self.assertIsNone(restarted.maybe_backup(now=1022))
            store.close()

    def test_lock_prevents_parallel_backup(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            scheduler = BackupScheduler(store, Path(d) / "backups",
                                        interval_seconds=0, keep=2)
            lock = Path(d) / "backups" / ".backup.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text("held", encoding="utf-8")
            self.assertIsNone(scheduler.maybe_backup(now=1000))
            store.close()

    def test_restored_backup_serves_query_and_context(self):
        from memory_engine.engine import MemoryEngine
        from v1_5_consolidator.contracts import SourceEnvelope
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            scheduler = BackupScheduler(store, Path(d) / "backups",
                                        interval_seconds=0, keep=2)
            backup = scheduler.maybe_backup(now=1000)
            restored = V15Store(str(backup))
            restored_engine = MemoryEngine(restored)
            self.assertEqual(len(restored_engine.retrieve("s", "茶")), 1)
            self.assertEqual(len(restored_engine.build_context("s", "茶")["items"]), 1)
            restored.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
