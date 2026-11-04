"""崩溃恢复演练：写库过程中强杀进程，检查库完整性、重启续跑与不重复采集。

输出 reports/crash_recovery.json。
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PLUGIN_DIR = ROOT / 'astrbot_plugin_digital_memory_store'
sys.path.insert(0, str(ROOT))

CHILD_FILE = HERE / 'crash_child.py'


def main():
    report = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S')}
    with tempfile.TemporaryDirectory(prefix='crash-') as tmp:
        tmp = Path(tmp)
        marker = tmp / 'progress.txt'
        environment = dict(os.environ, DLM_PLUGIN_DIR=str(PLUGIN_DIR),
                           DLM_ROOT=str(tmp), DLM_MARKER=str(marker))
        process = subprocess.Popen([sys.executable, str(CHILD_FILE)], env=environment)
        try:
            deadline = time.monotonic() + 30
            written = 0
            while time.monotonic() < deadline:
                if marker.is_file():
                    written = int(marker.read_text(encoding='utf-8') or 0)
                    if written >= 20:
                        break
                time.sleep(0.01)
            process.kill()
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()

        db = tmp / 'manual_test_memory.sqlite3'
        conn = sqlite3.connect(str(db))
        try:
            integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
            rows = conn.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            valid = conn.execute(
                "SELECT COUNT(*) FROM dl_capture WHERE length(content_sha)=64").fetchone()[0]
        finally:
            conn.close()

        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.storage import Store

        class Enc:
            model_key = 'crash-no-model'

            def __init__(self, path):
                pass

            def encode_documents(self, texts):
                raise RuntimeError('no model')

            def encode_queries(self, texts):
                raise RuntimeError('no model')

        store = Store(str(tmp), '', Enc)
        try:
            scope = store.scope('1', 'crash')
            before = store.flow.db.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            store.capture(scope, 'm999999', '关于编号999999的安排定在周五下午',
                          recorded_at='2025-01-01T11:00:00+00:00')
            process_jobs(store.flow.db)
            store.capture(scope, 'm1', '关于编号1的安排定在周五下午',
                          recorded_at='2025-01-01T10:01:00+00:00')
            after = store.flow.db.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            duplicate = store.flow.db.execute(
                "SELECT COUNT(*) FROM dl_capture WHERE content='关于编号1的安排定在周五下午'"
            ).fetchone()[0]
            jobs = {row[0]: row[1] for row in store.flow.db.execute(
                'SELECT status,COUNT(*) FROM dl_auto_jobs GROUP BY status')}
            report.update({
                'integrity': integrity,
                'killed_after_marker': written,
                'rows_after_kill': rows,
                'rows_with_sha': valid,
                'rows_before_reopen': before,
                'resume_capture_ok': after == before + 1,
                'duplicate_rows_for_same_message': duplicate,
                'jobs': jobs,
                'ok': (integrity == 'ok' and valid == rows and duplicate == 1
                       and after == before + 1 and jobs.get('dead', 0) == 0),
            })
        finally:
            store.close()

    out = ROOT / 'reports' / 'crash_recovery.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
