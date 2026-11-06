"""V6/V11：时间、顺序、长度与预算边界。

覆盖：
- 查询 399/400/401 字（>400 应静默不注入）；
- 预算 149/150/2000/2001 的 clamp（149→450，150→150，2000→2000，2001→450）；
- 改期相邻窗口 179/180/181 秒（≤180 成组，>180 不成组）；
- 未来原话不注入、当前消息不自引；
- 乱序到达时“最近原话”是否按时间而不是 seq 标注。

产物：reports/verification_v6_boundaries.json
用法：python verification/v6_boundaries.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

import sim_astrbot as sim  # noqa: E402

MODEL_DIR = str(C.DEV.parent / 'testbed' / 'models' / 'bge-small-zh-v1.5')


async def drain(runtime, tries=120):
    for _ in range(tries):
        await runtime.drain_auto()
        if await runtime.jobs('pending') == 0:
            return True
        await asyncio.sleep(0.01)
    return False


async def in_pool(runtime, fn):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(runtime.plugin.pool, fn)


async def query(runtime, text, sender, origin, message_id):
    event = sim.PrivateEvent(text, message_id, sender=str(sender), origin=origin)
    return await runtime.llm_request(event)


async def length_cases():
    results = []
    with C.TempRoot(prefix='dl-v6-len-') as root:
        runtime = sim.SimRuntime(root, C.make_config([42], budget=600, model_dir=MODEL_DIR))
        await runtime.start()
        try:
            await runtime.receive('边界长度：周末去书店找资料', message_id='seed')
            await drain(runtime)
            base = '周末去书店'
            for target in (399, 400, 401):
                text = base + '啊' * (target - len(base))
                injection = await query(runtime, text, 42, 'test:FriendMessage:42',
                                        'len-%d' % target)
                results.append({'target': target, 'actual_len': len(text),
                                'injected': injection.is_memory,
                                'parts': injection.count})
        finally:
            await runtime.stop()
    return results


def budget_cases():
    module, _, _ = sim.load()
    results = []
    for value, expected, valid in ((149, 450, False), (150, 150, True),
                                   (2000, 2000, True), (2001, 450, False)):
        plugin = module.DigitalMemoryStore(None, {
            'enabled': True, 'allowed_sender_ids': '42',
            'context_budget_chars': value})
        results.append({'input': value, 'effective': plugin.context_budget_chars,
                        'valid': plugin.context_budget_valid,
                        'expected': expected, 'expected_valid': valid,
                        'pass': plugin.context_budget_chars == expected
                                and plugin.context_budget_valid is valid})
    return results


def correction_cases():
    async def run():
        results = []
        config = C.make_config([50, 51, 52], budget=600, model_dir=MODEL_DIR)
        with C.TempRoot(prefix='dl-v6-cor-') as root:
            runtime = sim.SimRuntime(root, config)
            await runtime.start()
            try:
                base = datetime(2025, 1, 1, tzinfo=timezone.utc)
                for delta in (179, 180, 181):
                    sender = 50 + (delta - 179)
                    origin = 'test:FriendMessage:%d' % sender
                    scope = C.scope_of(sender, origin)
                    await runtime.receive('原计划周六去书店找资料',
                                          message_id='old-%d' % delta,
                                          sender=str(sender), origin=origin)
                    await runtime.receive('改到周日去书店',
                                          message_id='new-%d' % delta,
                                          sender=str(sender), origin=origin)
                    old_time = base.isoformat()
                    new_time = (base + timedelta(seconds=delta)).isoformat()
                    await runtime.execute(
                        "UPDATE dl_capture SET recorded_at=? WHERE scope=? AND content=?",
                        (old_time, scope, '原计划周六去书店找资料'))
                    await runtime.execute(
                        "UPDATE dl_capture SET recorded_at=? WHERE scope=? AND content=?",
                        (new_time, scope, '改到周日去书店'))

                    def read_peer(scope=scope):
                        store = runtime.plugin.store
                        row = store.flow.db.execute(
                            'SELECT seq,content,content_sha,recorded_at,state '
                            'FROM dl_capture WHERE scope=? ORDER BY seq LIMIT 1',
                            (scope,)).fetchone()
                        peer = store._possible_update_peer(scope, row)
                        return dict(peer) if peer else None

                    peer = await in_pool(runtime, read_peer)
                    results.append({'delta_s': delta,
                                    'peer': bool(peer),
                                    'peer_text': peer['content'] if peer else None,
                                    'pass': (delta <= 180) == bool(peer)})
            finally:
                await runtime.stop()
        return results
    return asyncio.run(run())


def future_and_current():
    async def run():
        out = {}
        config = C.make_config([60, 61], budget=600, model_dir=MODEL_DIR)
        with C.TempRoot(prefix='dl-v6-time-') as root:
            runtime = sim.SimRuntime(root, config)
            await runtime.start()
            try:
                now = datetime.now(timezone.utc)
                await runtime.receive('正常记忆：周末去书店', message_id='normal',
                                      sender='60', origin='test:FriendMessage:60')
                await runtime.receive('未来记忆：火星基地开放', message_id='future',
                                      sender='60', origin='test:FriendMessage:60')
                await runtime.execute(
                    "UPDATE dl_capture SET recorded_at=? WHERE scope=? AND content=?",
                    ((now + timedelta(days=1)).isoformat(),
                     C.scope_of(60, 'test:FriendMessage:60'), '未来记忆：火星基地开放'))
                await drain(runtime)
                future_injection = await query(runtime, '火星基地', 60,
                                              'test:FriendMessage:60', 'future-q')
                out['future'] = {
                    'injected': future_injection.is_memory,
                    'future_text_present': '火星基地' in future_injection.text,
                    'pass': '火星基地' not in future_injection.text,
                }

                await runtime.receive('当前消息唯一文本：月亮背面基地', message_id='cur-1',
                                      sender='61', origin='test:FriendMessage:61')
                await drain(runtime)
                current_injection = await query(runtime, '当前消息唯一文本：月亮背面基地', 61,
                                                'test:FriendMessage:61', 'cur-1')
                out['current'] = {
                    'injected': current_injection.is_memory,
                    'self_text_present': '月亮背面基地' in current_injection.text,
                    'pass': '月亮背面基地' not in current_injection.text,
                }
            finally:
                await runtime.stop()
        return out
    return asyncio.run(run())


def out_of_order():
    async def run():
        # 不用本地模型：2 维替身会把所有无“书”的句子都判成同向量，
        # 这里要测的是“按 seq 排序却标成最近”的背景包行为。
        config = C.make_config([62], budget=600, model_dir='')
        with C.TempRoot(prefix='dl-v6-order-') as root:
            runtime = sim.SimRuntime(root, config)
            await runtime.start()
            try:
                now = datetime.now(timezone.utc)
                await runtime.receive('乱序甲：下周三去爬山', message_id='a',
                                      sender='62', origin='test:FriendMessage:62')
                await runtime.receive('乱序乙：昨天去了海边', message_id='b',
                                      sender='62', origin='test:FriendMessage:62')
                scope = C.scope_of(62, 'test:FriendMessage:62')
                # 甲先到（seq 小）但时间更晚；乙后到（seq 大）但时间更早。
                await runtime.execute(
                    "UPDATE dl_capture SET recorded_at=? WHERE scope=? AND content=?",
                    ((now - timedelta(minutes=10)).isoformat(), scope, '乱序甲：下周三去爬山'))
                await runtime.execute(
                    "UPDATE dl_capture SET recorded_at=? WHERE scope=? AND content=?",
                    ((now - timedelta(hours=1)).isoformat(), scope, '乱序乙：昨天去了海边'))
                await drain(runtime)
                injection = await query(runtime, '今天天气怎么样', 62,
                                        'test:FriendMessage:62', 'order-q')
                text = injection.text
                body = injection.body()
                data = json.loads(body) if body else {}
                rows = data.get('最近原话') or data.get('可能相关原话') or []
                times = []
                for item in rows:
                    stamp = item.get('记录时间')
                    if isinstance(stamp, str):
                        try:
                            times.append(datetime.fromisoformat(stamp))
                        except ValueError:
                            pass
                labelled_latest = rows[-1].get('记录时间') if rows else None
                latest_time = max(times).isoformat() if times else None
                return {
                    'injected': injection.is_memory,
                    'rows': len(rows),
                    'labelled_latest': labelled_latest,
                    'actual_latest': latest_time,
                    'pass': bool(rows) and labelled_latest == latest_time,
                }
            finally:
                await runtime.stop()
    return asyncio.run(run())


def main():
    lengths = asyncio.run(length_cases())
    budgets = budget_cases()
    corrections = correction_cases()
    time_cases = future_and_current()
    order = out_of_order()

    checks = {
        'length_399': lengths[0]['injected'],
        'length_400': lengths[1]['injected'],
        'length_401': not lengths[2]['injected'],
        'budgets': all(row['pass'] for row in budgets),
        'correction_179': corrections[0]['peer'],
        'correction_180': corrections[1]['peer'],
        'correction_181': not corrections[2]['peer'],
        'future_excluded': time_cases['future']['pass'],
        'current_excluded': time_cases['current']['pass'],
        'out_of_order_time_label': order['pass'],
    }
    payload = {
        'suite': 'v6-boundaries',
        'length_cases': lengths,
        'budget_cases': budgets,
        'correction_cases': corrections,
        'time_cases': time_cases,
        'out_of_order': order,
        'checks': checks,
        'pass': all(checks.values()),
    }
    out = C.DEV / 'reports' / 'verification_v6_boundaries.json'
    C.dump_json(out, payload)
    print(json.dumps({'checks': checks, 'pass': payload['pass'], 'report': str(out)},
                     ensure_ascii=False, indent=2))
    return 0 if payload['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
