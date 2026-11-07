import sqlite3
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_digital_memory_store.storage import Store
from astrbot_plugin_digital_memory_store.store.safety import (
    SchemaTooNewError, StoreCorruptError, StoreLockedError)
from .test_store import FakeEncoder


class TestDataSafety(unittest.TestCase):
    def test_second_store_lock_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            a = Store(d, '', FakeEncoder)
            try:
                with self.assertRaises(StoreLockedError):
                    Store(d, '', FakeEncoder)
            finally:
                a.close()
            b = Store(d, '', FakeEncoder)
            b.close()

    def test_newer_schema_is_refused_before_open(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'manual_test_memory.sqlite3'
            db = sqlite3.connect(str(path))
            db.execute("CREATE TABLE dl_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO dl_meta VALUES('schema_version','999')")
            db.commit()
            db.close()
            with self.assertRaises(SchemaTooNewError):
                Store(d, '', FakeEncoder)

    def test_corrupt_database_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'manual_test_memory.sqlite3'
            path.write_bytes(b'not a sqlite database')
            with self.assertRaises(StoreCorruptError):
                Store(d, '', FakeEncoder)

    def test_legacy_database_gets_pre_migration_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'manual_test_memory.sqlite3'
            db = sqlite3.connect(str(path))
            db.execute('PRAGMA application_id=1279870533')
            db.commit()
            db.close()
            store = Store(d, '', FakeEncoder)
            try:
                backups = list((Path(d) / 'backups').glob('memory_*.sqlite3'))
                self.assertEqual(len(backups), 1)
                check = sqlite3.connect(str(backups[0]))
                try:
                    self.assertEqual(check.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                finally:
                    check.close()
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
