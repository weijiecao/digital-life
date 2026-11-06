"""V5：跨作用域差分（O2）。

50 个 scope，每个写入 10 条带唯一 canary 的原话；每个 scope 发 20 个相同问题的请求。
检查任何 packet 都不含其他 scope 的 canary，且数据库行只属于本 scope。

产物：reports/verification_v5_scope_diff.json
用法：python verification/v5_scope_diff.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

import sim_astrbot as sim  # noqa: E402

SCOPES = 50
MESSAGES = 10
QUERIES = 20


def canary(scope_index, message_index):
    return '[C%02dJ%02d]' % (scope_index, message_index)


async def run(root):
    config = C.make_config(
        range(SCOPES), budget=600,
        model_dir=str(C.DEV.parent / 'testbed' / 'models' / 'bge-small-zh-v1.5'))
    runtime = sim.SimRuntime(root, config)
    await runtime.start()
    packets = []
    try:
        for i in range(SCOPES):
            for j in range(MESSAGES):
                text = '%s 第%d号周末去书店找第%d本资料' % (canary(i, j), i, j)
                await runtime.receive(text, message_id='m-%02d-%02d' % (i, j),
                                      sender=str(i), origin='test:FriendMessage:%d' % i)
        for _ in range(200):
            await runtime.drain_auto()
            if await runtime.jobs('pending') == 0:
                break
            await asyncio.sleep(0.01)
        for i in range(SCOPES):
            for k in range(QUERIES):
                event = sim.PrivateEvent('周末去书店找资料', 'q-%02d-%02d' % (i, k),
                                         sender=str(i), origin='test:FriendMessage:%d' % i)
                injection = await runtime.llm_request(event)
                packets.append({'scope': i, 'text': injection.text,
                                'is_memory': injection.is_memory})
        rows = await runtime.query(
            'SELECT scope,COUNT(*) FROM dl_capture GROUP BY scope ORDER BY scope')
    finally:
        await runtime.stop()
    return packets, rows


def main():
    with C.TempRoot(prefix='dl-v5-') as root:
        packets, rows = asyncio.run(run(root))

    pattern = re.compile(r'\[C(\d{2})J\d{2}\]')
    cross, own_hit, empty = [], 0, 0
    for item in packets:
        if not item['is_memory']:
            empty += 1
            continue
        found = {int(match) for match in pattern.findall(item['text'])}
        if item['scope'] in found:
            own_hit += 1
        for other in sorted(found - {item['scope']}):
            if len(cross) < 20:
                cross.append({'scope': item['scope'], 'other': other})
    scope_counts = {row[0]: row[1] for row in rows}
    counts_ok = all(scope_counts.get(C.scope_of(i, 'test:FriendMessage:%d' % i)) == MESSAGES
                    for i in range(SCOPES))
    payload = {
        'suite': 'v5-scope-diff',
        'scopes': SCOPES, 'messages_per_scope': MESSAGES, 'packets': len(packets),
        'empty_packets': empty, 'own_canary_hits': own_hit,
        'cross_scope_hits': len(cross), 'cross_samples': cross,
        'scope_row_counts_ok': counts_ok,
        'pass': not cross and counts_ok and empties_ok(empty, packets),
    }
    out = C.DEV / 'reports' / 'verification_v5_scope_diff.json'
    C.dump_json(out, payload)
    print(json.dumps({'packets': len(packets), 'empty': empty,
                      'own_hits': own_hit, 'cross': len(cross),
                      'counts_ok': counts_ok, 'pass': payload['pass'],
                      'report': str(out)}, ensure_ascii=False, indent=2))
    return 0 if payload['pass'] else 1


def empties_ok(empty, packets):
    # 允许少数空包（例如池争用），但不能大面积失败；跨 scope 才是硬判据。
    return empty <= max(5, len(packets) // 100)


if __name__ == '__main__':
    raise SystemExit(main())
