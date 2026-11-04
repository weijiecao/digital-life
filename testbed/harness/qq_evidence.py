"""QQ 实测取证：只读插件库与 mock provider 日志，输出可引用的事实。

用法（在 testbed 下，QQ 消息发完之后）：
    .venv\\Scripts\\python.exe harness\\qq_evidence.py --out ..\\dev\\reports\\qq_real.json

不读取真实私人聊天以外的内容，不打印 API key（本脚本根本不碰 provider 配置）。
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
INSTANCE = TESTBED / 'instance'
PLUGIN_DATA = INSTANCE / 'data/plugin_data/astrbot_plugin_digital_memory_store'
MOCK_LOG = TESTBED / 'logs/llm_requests.jsonl'
PREFIX = '[DL_MEMORY_V040]'


def captures():
    path = PLUGIN_DATA / 'manual_test_memory.sqlite3'
    if not path.is_file():
        return []
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT seq,scope,content,recorded_at,state FROM dl_capture ORDER BY seq').fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def jobs():
    path = PLUGIN_DATA / 'manual_test_memory.sqlite3'
    if not path.is_file():
        return {}
    conn = sqlite3.connect(str(path))
    try:
        return {row[0]: row[1] for row in conn.execute(
            'SELECT status,COUNT(*) FROM dl_auto_jobs GROUP BY status')}
    finally:
        conn.close()


def requests():
    if not MOCK_LOG.is_file():
        return []
    rows = []
    for line in MOCK_LOG.read_text(encoding='utf-8').splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def extract_packet(content):
    """从注入文本里取出插件写的那段 JSON（前缀后面紧跟一个对象）。"""
    index = content.find(PREFIX)
    if index < 0:
        return None
    tail = content[index:]
    cursor = tail.find('{')
    while cursor != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(tail[cursor:])
        except ValueError:
            obj = None
        if isinstance(obj, dict) and ('待审记录序号' in obj or '未整理历史原话' in obj):
            return obj
        cursor = tail.find('{', cursor + 1)
    return None


def summarize_request(record, capture_texts):
    payload = record.get('payload') or {}
    body = json.dumps(payload, ensure_ascii=False)
    user_texts, packet = [], None
    for message in payload.get('messages') or []:
        if not isinstance(message, dict):
            continue
        content = message.get('content')
        if isinstance(content, str):
            if message.get('role') == 'user':
                user_texts.append(content)
            if packet is None:
                packet = extract_packet(content)
        elif isinstance(content, list):
            text = ''.join(str(part.get('text', '')) for part in content
                           if isinstance(part, dict))
            if message.get('role') == 'user':
                user_texts.append(text)
            if packet is None:
                packet = extract_packet(text)
    packet = packet if isinstance(packet, dict) else {}
    return {
        'at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.get('at', 0))),
        'has_memory_prefix': PREFIX in body,
        'injected_seq': packet.get('待审记录序号'),
        'injected_original': packet.get('未整理历史原话'),
        'captured_texts_in_body': [text for text in capture_texts if text and text in body],
        'body_chars': len(body),
        'last_user_text': (user_texts[-1][:120] if user_texts else ''),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(TESTBED.parent / 'dev/reports/qq_real.json'))
    args = parser.parse_args()

    rows = captures()
    texts = [row['content'] for row in rows]
    records = requests()
    summary = [summarize_request(record, texts) for record in records[-40:]]
    injected = [item for item in summary if item['has_memory_prefix']]

    # 只保留"注入过原话"的请求作为证据；按序号区分是哪条原话。
    scopes = {}
    for row in rows:
        scope = row['scope']
        entry = scopes.setdefault(scope, {'rows': 0, 'first_at': row['recorded_at'],
                                          'first_seq': row['seq']})
        entry['rows'] += 1
        entry['last_at'] = row['recorded_at']

    facts = {
        'suite': 'real-qq-evidence-v1',
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'captures': {
            'rows': len(rows),
            'scopes': scopes,
            'contents': texts[-20:],
            'states': sorted({row['state'] for row in rows}),
        },
        'jobs': jobs(),
        'provider': {
            'requests_seen': len(records),
            'injected_rounds': len(injected),
            'injected_samples': injected[-3:],
            'latest_injected': injected[-1] if injected else None,
        },
    }
    newest = max(scopes.items(), key=lambda item: item[1]['first_seq']) if scopes else None
    facts['captures']['latest_scope'] = (
        {'scope': newest[0], **newest[1]} if newest else None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')

    print('采集：%d 条；scope 数：%d；状态：%s'
          % (facts['captures']['rows'], len(facts['captures']['scopes']),
             ','.join(facts['captures']['states']) or '-'))
    print('最近原话：')
    for text in facts['captures']['contents'][-6:]:
        print('  -', text[:80])
    print('provider 请求：%d 条；含记忆片段：%d 条'
          % (facts['provider']['requests_seen'], facts['provider']['injected_rounds']))
    for item in facts['provider']['injected_samples']:
        print('  * %s 命中原话=%s 请求体=%d字'
              % (item['at'], item['captured_texts_in_body'], item['body_chars']))
    print('后台任务：', facts['jobs'])
    print('已写入', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
