"""V12：写时备份一致性。

一个 Store 在后台线程持续写入，另一个 Store 并发做 10 次备份；
逐个校验备份、统计行数、最后恢复到一个新目录并核对。

产物：reports/verification_v12_backup.json
用法：python verification/v12_backup.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402
from astrbot_plugin_digital_memory_store.panel import rotate_backups  # noqa: E402
from astrbot_plugin_digital_memory_store.recovery import (  # noqa: E402
    restore_to_new_directory, verify_backup)


def rotation_case():
    """校验过的备份再做轮转：真实备份不能被 -wal/-shm 挤掉。"""
    with tempfile.TemporaryDirectory(prefix='dl-v12-rotate-') as root:
        root = Path(root)
        store = Store(root, '')
        try:
            store.capture('manual-test:v12r', 'm1', '轮转测试：周末去书店')
            for _ in range(3):
                name = store.backup()
                store.verify_stored_backup(name)
                time.sleep(0.01)
            result = rotate_backups(root, keep=2)
            files = sorted(p.name for p in (root / 'backups').iterdir())
        finally:
            store.flow.db.close()
        real = [name for name in files if name.endswith('.sqlite3')]
        side = [name for name in files if name.endswith(('-wal', '-shm', '-journal'))]
        return {'files': files, 'real_backups': real, 'sidecar': side,
                'rotate': result,
                'pass': len(real) == 2 and not side}


def count_rows(db):
    # 备份是单文件快照：加 immutable=1，避免审计本身生成 -wal/-shm。
    conn = sqlite3.connect('file:%s?mode=ro&immutable=1' % Path(db).as_posix(), uri=True)
    try:
        return conn.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
    finally:
        conn.close()


def main():
    with tempfile.TemporaryDirectory(prefix='dl-v12-') as root:
        root = Path(root)
        reader = Store(root, '')
        writer_errors = []

        def writer():
            try:
                store = Store(root, '')
                try:
                    for index in range(300):
                        store.capture('manual-test:v12', 'w-%03d' % index,
                                      '并发写入第%03d条：周末去书店' % index)
                        time.sleep(0.002)
                finally:
                    store.flow.db.close()
            except Exception as exc:  # noqa: BLE001 - 记录而不是吞掉
                writer_errors.append('%s: %s' % (type(exc).__name__, exc))

        thread = threading.Thread(target=writer, name='v12-writer')
        thread.start()
        time.sleep(0.02)
        backups = []
        while thread.is_alive() and len(backups) < 10:
            name = reader.backup()
            path = root / 'backups' / name
            # 用公开的“校验已有备份”接口，而不是绕过它直接 verify_backup。
            verification = reader.verify_stored_backup(name)
            backups.append({'name': name, 'rows': count_rows(path),
                            'ok': verification['ok'],
                            'errors': verification['errors'],
                            'bytes': path.stat().st_size})
            time.sleep(0.005)
        thread.join()

        final = reader.backup()
        final_path = root / 'backups' / final
        final_verify = reader.verify_stored_backup(final)
        final_rows = count_rows(final_path)

        restore_parent = Path(tempfile.mkdtemp(prefix='dl-v12-restore-'))
        restored = restore_parent / 'restore'
        try:
            result = restore_to_new_directory(final_path, restored)
            restore_path = restored / 'manual_test_memory.sqlite3'
            restore_rows = count_rows(restore_path) if restore_path.exists() else None
        finally:
            pass

        residue = sorted(p.name for p in (root / 'backups').iterdir()
                         if p.name.endswith(('-wal', '-shm', '-journal')))
        rows = [item['rows'] for item in backups]
        checks = {
            'writer_ok': not writer_errors,
            'ten_backups': len(backups) == 10,
            'all_verify': all(item['ok'] for item in backups),
            'monotonic_rows': rows == sorted(rows),
            'final_integrity': final_verify['ok'],
            'final_rows': final_rows == 300,
            'restore_rows': restore_rows == final_rows,
            'no_sidecar': not residue,
        }
        payload = {
            'suite': 'v12-backup',
            'checks': checks, 'writer_errors': writer_errors,
            'backups': backups,
            'final': {'name': final, 'rows': final_rows,
                      'ok': final_verify['ok'], 'errors': final_verify['errors']},
            'restore': {'result': result, 'rows': restore_rows},
            'sidecar': residue,
            'rotation': rotation_case(),
            'pass': all(checks.values()),
        }
        payload['checks']['rotation_keeps_real_backups'] = payload['rotation']['pass']
        payload['pass'] = all(payload['checks'].values())
        reader.flow.db.close()
        out = C.DEV / 'reports' / 'verification_v12_backup.json'
        C.dump_json(out, payload)
        print(json.dumps({'checks': checks, 'rows': rows, 'final_rows': final_rows,
                          'restore_rows': restore_rows, 'pass': payload['pass'],
                          'report': str(out)}, ensure_ascii=False, indent=2))
        return 0 if payload['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
