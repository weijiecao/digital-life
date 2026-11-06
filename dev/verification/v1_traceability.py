"""V1：注入溯源审计（O1）。

在替身运行时生成 N 个 scope × M 条原话 × K 个请求，收集全部注入 packet，
逐项检查：每条“原话”能否指回同 scope 的 dl_capture、时间是否早于请求、
序号是否存在、是否跨 scope、是否有无法归类的字符串。

产物：reports/verification_v1_traceability.json
用法：python verification/v1_traceability.py [--scopes 50] [--messages 20] [--queries 20]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

import sim_astrbot as sim  # noqa: E402

sys.path.insert(0, str(C.DEV / 'scripts'))
import memory_audit as audit  # noqa: E402


async def generate(root, scopes, messages, queries):
    config = C.make_config(
        range(scopes), budget=600,
        model_dir=str(C.DEV.parent / 'testbed' / 'models' / 'bge-small-zh-v1.5'))
    runtime = sim.SimRuntime(root, config)
    await runtime.start()
    packets = []
    started = time.time()
    try:
        for i in range(scopes):
            for j in range(messages):
                text = '第%d号计划第%d条：周末去书店找第%d本编程资料，编号%d-%d' % (i, j, j, i, j)
                await runtime.receive(text, message_id='m-%d-%d' % (i, j),
                                      sender=str(i), origin='test:FriendMessage:%d' % i)
        # 等后台整理与字面索引就绪；单线程池有积压时查询会 1.5 s 超时，
        # 因此必须排空后再发请求，否则测到的是池争用而不是溯源。
        for _ in range(300):
            await runtime.drain_auto()
            if await runtime.jobs('pending') == 0 and await runtime.jobs('dead') == 0:
                break
            await asyncio.sleep(0.01)

        for i in range(scopes):
            for k in range(queries):
                query = '周末去书店找第%d本' % (k % messages)
                event = sim.PrivateEvent(query, 'q-%d-%d' % (i, k),
                                         sender=str(i), origin='test:FriendMessage:%d' % i)
                injection = await runtime.llm_request(event)
                packets.append({
                    'scope': C.scope_of(i, 'test:FriendMessage:%d' % i),
                    'text': C.packet_text(injection),
                    'request_time': C.request_time(),
                    'empty': not injection.is_memory,
                })
        rows = (await runtime.scalar('SELECT COUNT(*) FROM dl_capture')) or 0
    finally:
        await runtime.stop()
    return packets, rows, round(time.time() - started, 2)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scopes', type=int, default=50)
    parser.add_argument('--messages', type=int, default=20)
    parser.add_argument('--queries', type=int, default=20)
    args = parser.parse_args(argv)

    with C.TempRoot(prefix='dl-v1-') as root:
        packets, rows, elapsed = asyncio.run(
            generate(root, args.scopes, args.messages, args.queries))
        db = C.db_path(root)
        database = audit.audit_database(db)
        packets_path = Path(root) / 'packets.jsonl'
        packets_path.write_text(
            '\n'.join(json.dumps(p, ensure_ascii=False) for p in packets), encoding='utf-8')
        result = audit.audit_packets(db, packets_path)

    payload = {
        'suite': 'v1-traceability',
        'config': {'scopes': args.scopes, 'messages': args.messages,
                   'queries': args.queries, 'captures': rows, 'packets': len(packets),
                   'empty_packets': sum(1 for p in packets if p['empty'])},
        'elapsed_s': elapsed,
        'database': {'errors': database['errors'], 'warnings': database['warnings'],
                     'info': [x for x in database['info'] if x['check'] != 'duplicate_content']},
        'packets': result,
        'pass': (not database['errors'] and result['errors'] == 0
                 and sum(1 for p in packets if p['empty']) == 0),
    }
    out = C.DEV / 'reports' / 'verification_v1_traceability.json'
    C.dump_json(out, payload)
    print(json.dumps({
        'captures': rows, 'packets': len(packets),
        'empty': payload['config']['empty_packets'],
        'quotes': result['quotes'], 'titles': result['titles'],
        'seq_refs': result['seq_refs'],
        'errors': result['errors'], 'pass': payload['pass'],
        'report': str(out),
    }, ensure_ascii=False, indent=2))
    return 0 if payload['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
