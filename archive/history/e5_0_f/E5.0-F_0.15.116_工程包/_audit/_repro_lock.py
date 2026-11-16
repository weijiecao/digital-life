"""P0 最小复现：Store.backup() 生成的备份文件在 Windows 下仍被占用。

用法（在工程包根目录执行）：python _audit/_repro_lock.py
期望：修复前打印 LOCKED；修复后打印 unlink OK。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402
from test_store import FakeEncoder  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    a = Store(d, 'fixture-only', FakeEncoder)
    s = a.scope('1', 's')
    a.capture(s, '1', '备份锁验证', recorded_at='2025-01-01T00:00:00+00:00')
    name = a.backup()
    a.close()
    p = Path(d) / 'backups' / name
    print('backup file:', name, 'exists:', p.exists())
    try:
        p.unlink()
        print('RESULT: unlink OK (no leak)')
    except Exception as e:
        print('RESULT: LOCKED ->', type(e).__name__, e)
