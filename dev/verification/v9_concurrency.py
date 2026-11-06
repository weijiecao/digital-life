"""V9：双进程并发（SCP-03）与崩溃恢复复验。

1. 两个独立进程同时写同一个库（同 scope、不同 message_id）：
   检查是否丢写、报错、产生重复 event_hash、完整性是否 ok；
2. 调用既有 crash_recovery 演练，复验强杀后的完整性/续写/不重复。

产物：reports/verification_v9_concurrency.json
用法：python verification/v9_concurrency.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402

CHILD = """
import sys
from astrbot_plugin_digital_memory_store.storage import Store
s = Store(sys.argv[1], '')
try:
    for i in range(200):
        s.capture('manual-test:v9', '%s-%03d' % (sys.argv[2], i),
                  '%s 双进程写入第%03d条：周末去书店' % (sys.argv[2], i))
finally:
    s.close()
"""


def two_processes():
    with tempfile.TemporaryDirectory(prefix='dl-v9-') as root:
        root = Path(root)
        seed = Store(str(root), '')
        seed.flow.db.close()
        env = dict(os.environ)
        env['PYTHONPATH'] = str(C.DEV)
        processes = [subprocess.Popen(
            [sys.executable, '-c', CHILD, str(root), tag],
            env=env, cwd=str(C.DEV),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for tag in ('A', 'B')]
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=180)
            outputs.append({'rc': process.returncode,
                            'stdout': stdout[-200:], 'stderr': stderr[-300:]})

        conn = sqlite3.connect(str(root / 'manual_test_memory.sqlite3'))
        try:
            rows = conn.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            hashes = conn.execute(
                'SELECT COUNT(DISTINCT event_hash) FROM dl_capture').fetchone()[0]
            jobs = conn.execute('SELECT COUNT(*) FROM dl_auto_jobs').fetchone()[0]
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            dup = conn.execute(
                'SELECT COUNT(*) FROM (SELECT event_hash,COUNT(*) c '
                'FROM dl_capture GROUP BY event_hash HAVING c>1)').fetchone()[0]
        finally:
            conn.close()
        return {'rows': rows, 'distinct_hashes': hashes, 'jobs': jobs,
                'integrity': integrity, 'duplicate_groups': dup,
                'workers': outputs,
                'pass': all(item['rc'] == 0 for item in outputs)
                        and rows == 400 and hashes == 400 and jobs == 400
                        and integrity == 'ok' and dup == 0}


def crash_recovery():
    script = C.DEV / 'evaluation' / 'crash_recovery.py'
    started = time.time()
    process = subprocess.run(
        [sys.executable, str(script)], cwd=str(C.DEV),
        capture_output=True, text=True, timeout=180)
    report = C.DEV / 'reports' / 'crash_recovery.json'
    data = json.loads(report.read_text(encoding='utf-8')) if report.is_file() else None
    return {'returncode': process.returncode,
            'elapsed_s': round(time.time() - started, 2),
            'stderr_tail': process.stderr[-400:],
            'report': data,
            'pass': process.returncode == 0 and bool(data)}


def main():
    processes = two_processes()
    crash = crash_recovery()
    payload = {'suite': 'v9-concurrency', 'two_processes': processes,
               'crash_recovery': crash,
               'pass': processes['pass'] and crash['pass']}
    out = C.DEV / 'reports' / 'verification_v9_concurrency.json'
    C.dump_json(out, payload)
    print(json.dumps({'two_processes': {k: v for k, v in processes.items()
                                        if k != 'workers'},
                      'workers': processes['workers'],
                      'crash_rc': crash['returncode'],
                      'pass': payload['pass'], 'report': str(out)},
                     ensure_ascii=False, indent=2))
    return 0 if payload['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
