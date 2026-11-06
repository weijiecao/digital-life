"""验证用公共工具：构造替身运行时、计算 scope、导出 packet。

所有脚本只读/临时目录；不接触生产实例，不访问网络。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEV = Path(__file__).resolve().parents[1]
if str(DEV) not in sys.path:
    sys.path.insert(0, str(DEV))

import sim_astrbot as sim  # noqa: E402

DB_NAME = 'manual_test_memory.sqlite3'


def make_config(senders, *, budget=600, **overrides):
    config = {
        'enabled': True,
        'allowed_sender_ids': ','.join(str(s) for s in senders),
        'automatic_memory_enabled': True,
        'context_budget_chars': budget,
        'always_context_enabled': True,
        'latency_metrics_enabled': False,
    }
    config.update(overrides)
    return config


def scope_of(sender, origin):
    data = (str(len(str(sender))) + ':' + str(sender) + str(origin)).encode('utf-8')
    return 'manual-test:' + hashlib.sha256(data).hexdigest()


def db_path(root):
    return Path(root) / 'plugin_data' / 'astrbot_plugin_digital_memory_store' / DB_NAME


def packet_text(injection):
    return injection.text


def dump_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def request_time():
    # 比采集时间晚 1 秒，避免同一秒边界把合法原话误判为“未来”。
    return (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()


class TempRoot:
    def __init__(self, prefix='dl-verify-'):
        self._tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.path = self._tmp.name

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False
