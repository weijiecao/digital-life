"""P0 回归：备份文件在 Windows 上必须可删除、可恢复、可连续执行。

核查报告 P0-1 的根因是 `with sqlite3.connect()` 只提交不关闭，句柄泄漏锁住
`backups/*.sqlite3`，导致 5 项既有用例在临时目录清理时 WinError 32。
修复之后必须有测试锁住这个行为，否则下次同样写法会静默回归。

本文件只依赖离线环境，且在 Linux 上同样成立（句柄未关闭时删除也可能失败）。
"""

import os
import tempfile
import time
import unittest
from pathlib import Path

from .test_store import FakeEncoder

from astrbot_plugin_digital_memory_store.panel import rotate_backups
from astrbot_plugin_digital_memory_store.recovery import restore_to_new_directory
from astrbot_plugin_digital_memory_store.storage import Store

STAMP = '2025-01-01T00:00:00+00:00'


def make_store(directory):
    store = Store(directory, '', FakeEncoder)
    store.capture(store.scope('1', 's'), 'm1', '备份锁验证', recorded_at=STAMP)
    return store


class TestBackupHandleRegression(unittest.TestCase):
    def test_backup_file_is_deletable_after_close(self):
        with tempfile.TemporaryDirectory() as d:
            store = make_store(d)
            name = store.backup()
            store.close()
            path = Path(d) / 'backups' / name
            self.assertTrue(path.is_file())
            # 修复前这里在 Windows 上抛 PermissionError: [WinError 32]。
            path.unlink()
            self.assertFalse(path.exists())

    def test_ten_consecutive_backups_are_all_deletable(self):
        with tempfile.TemporaryDirectory() as d:
            store = make_store(d)
            try:
                names = [store.backup() for _ in range(10)]
                self.assertEqual(len(set(names)), 10)
                folder = Path(d) / 'backups'
                # 备份产物只应是一份 .sqlite3；-wal/-shm 属于校验残留，须清理。
                self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(names))
                # 备份函数返回时句柄就该已释放，不必等待 GC 或关闭主库。
                for name in names:
                    (folder / name).unlink()
                self.assertEqual(list(folder.iterdir()), [])
            finally:
                store.close()

    def test_backup_can_be_restored_to_new_directory(self):
        with tempfile.TemporaryDirectory() as d:
            store = make_store(d)
            name = store.backup()
            store.close()
            target = Path(d) / 'restored'
            report = restore_to_new_directory(Path(d) / 'backups' / name, target)
            self.assertTrue(report['ok'])
            self.assertTrue((target / 'manual_test_memory.sqlite3').is_file())
            # 恢复出的库可以独立打开并读到原话。
            restored = Store(target, '', FakeEncoder)
            try:
                rows = restored.flow.db.execute(
                    'SELECT content FROM dl_capture ORDER BY seq').fetchall()
                self.assertEqual([row[0] for row in rows], ['备份锁验证'])
            finally:
                restored.close()

    def test_backup_folder_removable_while_store_is_open(self):
        with tempfile.TemporaryDirectory() as d:
            store = make_store(d)
            try:
                name = store.backup()
                folder = Path(d) / 'backups'
                for suffix in ('', '-wal', '-shm'):
                    candidate = folder / (name + suffix)
                    if candidate.exists():
                        candidate.unlink()
                # 主库仍打开时能删掉备份目录，说明备份连接没有残留句柄。
                os.rmdir(folder)
                self.assertFalse(folder.exists())
            finally:
                store.close()

    def test_verify_stored_backup_leaves_no_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            store = make_store(d)
            try:
                name = store.backup()
                result = store.verify_stored_backup(name)
                self.assertTrue(result['ok'], result['errors'])
                folder = Path(d) / 'backups'
                # 校验可以读，但不能在备份目录留下 -wal/-shm。
                self.assertEqual(sorted(p.name for p in folder.iterdir()), [name])
            finally:
                store.close()

    def test_rotate_after_verify_keeps_real_backups(self):
        with tempfile.TemporaryDirectory() as d:
            store = make_store(d)
            names = []
            try:
                for _ in range(3):
                    name = store.backup()
                    names.append(name)
                    self.assertTrue(store.verify_stored_backup(name)['ok'])
                    time.sleep(0.01)
                result = rotate_backups(d, keep=2)
                folder = Path(d) / 'backups'
                files = sorted(p.name for p in folder.iterdir())
                real = [name for name in files if name.endswith('.sqlite3')]
                sidecars = [name for name in files if name.endswith(('-wal', '-shm', '-journal'))]
                self.assertEqual(sidecars, [])
                self.assertEqual(len(real), 2, '轮转必须保留 2 个真实备份，不能被旁文件挤掉')
                self.assertTrue(set(real).issubset(set(names)))
                self.assertEqual(result['remaining'], 2)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
