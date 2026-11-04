"""真实语料导入：把脱敏聊天导出写成评估用记忆库（真实 BGE 可选）。

输入格式（JSON 或 JSONL，每行一个会话）：
{
  "sessions": [
    {"id": "s1", "messages": [
      {"text": "我喜欢吃苹果", "at": "2025-01-01T10:00:00+08:00"},
      {"text": "周末去公园跑步", "at": "2025-01-01T10:01:00+08:00"}
    ]}
  ]
}

只导入文本消息；空串、超过 400 字、以及 role 不是 user 的消息会被跳过并计数。
输出：<db-dir>/manual_test_memory.sqlite3，供 real_corpus_eval.py 评分。

用法：
    python evaluation/real_corpus_import.py --input corpus.json \
        --model-dir ../testbed/models/bge-small-zh-v1.5 --db-dir reports/real_corpus_db
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from m2_runner import _factory, configure  # noqa: E402
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs  # noqa: E402
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402


def load_sessions(path):
    text = Path(path).read_text(encoding='utf-8').strip()
    if not text:
        raise ValueError('空文件')
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        sessions = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        if isinstance(payload, list):
            sessions = payload
        elif isinstance(payload, dict) and 'sessions' in payload:
            sessions = payload['sessions']
        elif isinstance(payload, dict) and 'messages' in payload:
            sessions = [payload]
        else:
            raise ValueError('无法识别的格式：需要 sessions 列表或 JSONL')
    if not isinstance(sessions, list) or not sessions:
        raise ValueError('没有会话')
    return sessions


def _timestamp(value):
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def import_corpus(sessions, db_dir, model_dir=''):
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    store = Store(str(db_dir), model_dir, _factory(model_dir))
    configure(store, model_dir)
    store.automatic_memory_enabled = False  # 导入阶段不建自动主题，保持语料原样
    stats = {'sessions': 0, 'messages': 0, 'imported': 0, 'skipped': 0, 'scopes': []}
    try:
        for session in sessions:
            sid = str(session.get('id') or ('session-%d' % (stats['sessions'] + 1)))
            scope = store.scope(sid, 'real')
            stats['sessions'] += 1
            stats['scopes'].append({'id': sid, 'scope': scope})
            for index, message in enumerate(session.get('messages') or []):
                stats['messages'] += 1
                text = message.get('text') if isinstance(message, dict) else message
                role = message.get('role') if isinstance(message, dict) else None
                if role is not None and role != 'user':
                    stats['skipped'] += 1
                    continue
                if not isinstance(text, str) or not text.strip() or len(text) > 400:
                    stats['skipped'] += 1
                    continue
                store.capture(scope, '%s-%d' % (sid, index), text.strip(),
                              recorded_at=_timestamp(message.get('at') if isinstance(message, dict) else None))
                stats['imported'] += 1
        process_jobs(store.flow.db)
    finally:
        store.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description='导入脱敏真实聊天语料')
    parser.add_argument('--input', required=True)
    parser.add_argument('--model-dir', default='')
    parser.add_argument('--db-dir', default=str(ROOT / 'reports' / 'real_corpus_db'))
    args = parser.parse_args()
    sessions = load_sessions(args.input)
    stats = import_corpus(sessions, args.db_dir, args.model_dir)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print('数据库：', Path(args.db_dir) / 'manual_test_memory.sqlite3')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
