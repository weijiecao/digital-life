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
            store.close()


if __name__ == "__main__":
    unittest.main()
