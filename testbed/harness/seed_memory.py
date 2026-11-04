"""向运行中的 testbed 记忆库补一条“旧记忆”，用于插件上下文归因实验。

直接写同一个 SQLite（WAL 模式，插件会读到），不会经过 AstrBot 会话历史，
因此这条内容只可能由插件检索到。

用法：
    python harness/seed_memory.py --text "记住：青柠计划的门禁暗号是7391" --days-ago 30
"""

import argparse
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
ROOT = TESTBED / 'instance/data/plugin_data/astrbot_plugin_digital_memory_store'
sys.path.insert(0, str(TESTBED / 'instance/data/plugins'))

from astrbot_plugin_digital_memory_store.auto_organize import process_jobs  # noqa: E402
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402


class _NoEncoder:
    model_key = 'seed-no-model'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        raise RuntimeError('no model')

    def encode_queries(self, texts):
        raise RuntimeError('no model')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--text', required=True)
    parser.add_argument('--days-ago', type=int, default=30)
    parser.add_argument('--root', default=str(ROOT))
    args = parser.parse_args(argv)
    store = Store(args.root, '', _NoEncoder)
    try:
        scope_row = store.flow.db.execute(
            'SELECT scope, COUNT(*) AS n FROM dl_capture GROUP BY scope ORDER BY n DESC LIMIT 1'
        ).fetchone()
        if not scope_row:
            raise SystemExit('库里没有会话，先发一条真实消息')
        scope = scope_row['scope']
        recorded = (datetime.now(timezone.utc) - timedelta(days=args.days_ago)).isoformat()
        message_id = 'seed-' + hashlib.sha256(args.text.encode()).hexdigest()[:12]
        store.capture(scope, message_id, args.text, recorded_at=recorded)
        process_jobs(store.flow.db)
        seq = store.flow.db.execute(
            'SELECT seq FROM dl_capture WHERE scope=? AND event_hash=?',
            (scope, hashlib.sha256((scope + '\0' + message_id).encode()).hexdigest()),
        ).fetchone()
        print('seeded scope=%s seq=%s recorded=%s' % (scope, seq['seq'] if seq else '?', recorded))
    finally:
        store.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
