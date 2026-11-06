"""V8：跨版本数据保全（1.0.3 → 1.0.14 → 回退到 1.0.3 读写）。

做法：解压旧版插件到临时目录，用旧版 Store 建库写 5 条；用当前 Store 打开、
再写 1 条；再用旧版 Store 打开、读并写 1 条；最后当前 Store 打开核对 7 条。
只验证“数据/基础 schema”兼容，不验证旧版 UI/命令。

产物：reports/verification_v8_upgrade.json
用法：python verification/v8_upgrade.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402

sys.path.insert(0, str(C.DEV / 'scripts'))
import memory_audit as audit  # noqa: E402

OLD_ZIP = C.DEV.parent / 'dist' / 'astrbot_plugin_digital_memory_store-1.0.3.zip'


def run_old(pkg, root, action):
    code = """
import sys
sys.path.insert(0, r'%s')
from astrbot_plugin_digital_memory_store.storage import Store
s = Store(r'%s', '')
sc = s.scope('42', 'test:FriendMessage:42')
if %r == 'create':
    for i in range(5):
        s.capture(sc, 'old-%%d' %% i, '旧版本记忆第%%d条：周末去书店' %% i)
else:
    s.capture(sc, 'old-extra', '旧版本追加：周末去书店')
print('OLD_ROWS', s.flow.db.execute('select count(*) from dl_capture').fetchone()[0])
s.close()
""" % (pkg, root, action)
    env = dict(os.environ)
    env['PYTHONPATH'] = str(pkg)
    env['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.run([sys.executable, '-c', code], env=env,
                          capture_output=True, text=True, timeout=120)


def main():
    if not OLD_ZIP.is_file():
        payload = {'suite': 'v8-upgrade', 'skipped': 'missing %s' % OLD_ZIP,
                   'pass': None}
        out = C.DEV / 'reports' / 'verification_v8_upgrade.json'
        C.dump_json(out, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix='dl-v8-') as root:
        root = Path(root)
        pkg = root / 'pkg'
        with zipfile.ZipFile(OLD_ZIP) as archive:
            archive.extractall(pkg)
        create = run_old(pkg, root, 'create')
        store = Store(str(root), '')
        try:
            rows_after_create = store.flow.db.execute(
                'SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            store.capture(store.scope('42', 'test:FriendMessage:42'),
                          'current-extra', '当前版本追加：周末去书店')
            rows_after_current = store.flow.db.execute(
                'SELECT COUNT(*) FROM dl_capture').fetchone()[0]
        finally:
            store.close()
        old_again = run_old(pkg, root, 'reopen')
        store = Store(str(root), '')
        try:
            rows_final = store.flow.db.execute(
                'SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            integrity = store.flow.db.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            store.close()
        audit_result = audit.audit_database(root / 'manual_test_memory.sqlite3')

    checks = {
        'old_create_rc': create.returncode == 0,
        'old_created_5': rows_after_create == 5,
        'current_reads_5': rows_after_create == 5,
        'current_writes_6': rows_after_current == 6,
        'old_reopen_rc': old_again.returncode == 0,
        'old_reopen_reads': 'OLD_ROWS 7' in old_again.stdout,
        'final_rows_7': rows_final == 7,
        'integrity': integrity == 'ok',
        'audit_no_errors': not audit_result['errors'],
    }
    payload = {
        'suite': 'v8-upgrade',
        'old_zip': str(OLD_ZIP),
        'versions': {'old': '1.0.3', 'current': '1.0.14'},
        'checks': checks,
        'rows': {'after_old': rows_after_create, 'after_current': rows_after_current,
                 'final': rows_final},
        'old_stdout': [create.stdout.strip(), old_again.stdout.strip()],
        'old_stderr': [create.stderr[-300:], old_again.stderr[-300:]],
        'audit_errors': audit_result['errors'],
        'pass': all(checks.values()),
    }
    out = C.DEV / 'reports' / 'verification_v8_upgrade.json'
    C.dump_json(out, payload)
    print(json.dumps({'checks': checks, 'rows': payload['rows'],
                      'pass': payload['pass'], 'report': str(out)},
                     ensure_ascii=False, indent=2))
    return 0 if payload['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
