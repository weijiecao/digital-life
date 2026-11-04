"""L0 演练：在「真实数据副本」上做备份 → 校验 → 恢复到新目录 → 删除。

不碰正在运行的实例库：先用 SQLite 在线备份把活库复制到临时目录，再在副本上演练。
输出 JSON 供 dev/reports/l0_drills.json 汇总。
"""

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

TESTBED = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTBED / 'instance' / 'data' / 'plugins'))

from astrbot_plugin_digital_memory_store.recovery import restore_to_new_directory  # noqa: E402
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402

LIVE_DB = (TESTBED / 'instance' / 'data' / 'plugin_data'
           / 'astrbot_plugin_digital_memory_store' / 'manual_test_memory.sqlite3')


class _NoEncoder:
    model_key = 'l0-drill-no-model'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        raise RuntimeError('semantic disabled in drill')

    def encode_queries(self, texts):
        raise RuntimeError('semantic disabled in drill')


def main():
    result = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'live_db': str(LIVE_DB)}
    if not LIVE_DB.is_file():
        result['ok'] = False
        result['error'] = 'live db missing'
    else:
        with tempfile.TemporaryDirectory(prefix='l0-drill-') as tmp:
            tmp = Path(tmp)
            copy = tmp / 'manual_test_memory.sqlite3'
            source = sqlite3.connect('file:%s?mode=ro' % LIVE_DB.as_posix(), uri=True)
            target = sqlite3.connect(str(copy))
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()

            started = time.perf_counter()
            store = Store(tmp, '', _NoEncoder)
            try:
                before = store.flow.db.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
                name = store.backup()
                verification = store.verify_stored_backup(name)
                backup_path = tmp / 'backups' / name
            finally:
                store.close()
            restored_dir = tmp / 'restored'
            restored = restore_to_new_directory(backup_path, restored_dir)
            reopened = Store(restored_dir, '', _NoEncoder)
            try:
                after = reopened.flow.db.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            finally:
                reopened.close()
            elapsed = round(time.perf_counter() - started, 2)
            backup_path.unlink()
            deleted = not backup_path.exists()
            result.update({
                'ok': bool(verification['ok'] and restored['ok'] and before == after and deleted),
                'captures_before': before,
                'captures_after_restore': after,
                'backup_verified': bool(verification['ok']),
                'restore_ok': bool(restored['ok']),
                'backup_deleted': deleted,
                'elapsed_s': elapsed,
                'checks': verification['checks'],
                'warnings': verification['warnings'],
            })
    out = Path(__file__).resolve().parents[2] / 'dev' / 'reports' / 'l0_drills.json'
    existing = {}
    if out.is_file():
        try:
            existing = json.loads(out.read_text(encoding='utf-8'))
        except ValueError:
            existing = {}
    existing['backup_drill'] = result
    out.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
