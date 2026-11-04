"""G3 窄路 A/B：指代追问（anaphora）15 条，开启候选筛选器后的端到端召回。

只使用 m2_corpus 里的**虚构**用例；真实 DeepSeek 调用仅发送这些虚构原话。
输出 reports/g3_ab_anaphora.json。

用法：python evaluation/g3_ab_anaphora.py [--key-file ...]
"""

import argparse
import asyncio
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import sim_astrbot as sim  # noqa: E402
from m2_corpus import build_cases  # noqa: E402

API = 'https://api.deepseek.com/v1/chat/completions'


class DeepSeekJudge:
    def __init__(self, key, model='deepseek-chat'):
        self.key = key
        self.model = model
        self.calls = []
        self.tokens = {'in': 0, 'out': 0}

    async def llm_generate(self, chat_provider_id=None, prompt=None, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._call, prompt)

    def _call(self, prompt):
        payload = {
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0,
            'max_tokens': 120,
            'response_format': {'type': 'json_object'},
        }
        request = urllib.request.Request(
            API, data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + self.key},
            method='POST')
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode('utf-8'))
        usage = body.get('usage') or {}
        self.tokens['in'] += usage.get('prompt_tokens', 0)
        self.tokens['out'] += usage.get('completion_tokens', 0)
        content = (body.get('choices') or [{}])[0].get('message', {}).get('content', '')
        self.calls.append({'latency_s': round(time.perf_counter() - started, 3),
                           'usage': usage})
        import types
        return types.SimpleNamespace(completion_text=content)


async def run_case(runtime, judge, case, index):
    origin = 'webchat:FriendMessage:g3case%02d' % index
    text = case['messages'][0][0]
    event = await runtime.receive(text, message_id='m-%02d' % index, origin=origin)
    await runtime.llm_request(event)  # 第一轮：建立历史（本地可能给也可能不给）
    followup = await runtime.receive('后来呢？', message_id='f-%02d' % index, origin=origin)
    injection = await runtime.llm_request(followup)
    parts = injection.text
    return {
        'id': case['id'], 'expected': case['expect'][0],
        'injected': bool(parts), 'hit': case['expect'][0] in parts,
        'chars': len(parts), 'latency_s': round(injection.elapsed, 3),
    }


async def run_negative(runtime, judge, case, index):
    """负例：没有任何相关记忆，开了筛选器也不得注入。"""
    origin = 'webchat:FriendMessage:g3neg%02d' % index
    for offset, (text, stamp) in enumerate(case['messages']):
        await runtime.receive(text, message_id='n-%02d-%d' % (index, offset), origin=origin)
    event = await runtime.receive(case['query'], message_id='q-%02d' % index, origin=origin)
    injection = await runtime.llm_request(event)
    return {
        'id': case['id'], 'injected': bool(injection.text),
        'chars': len(injection.text), 'latency_s': round(injection.elapsed, 3),
        'expected_empty': True,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file', default=str(
        ROOT.parent / 'testbed' / 'instance' / 'data' / '.deepseek_key'))
    parser.add_argument('--out', default='')
    parser.add_argument('--kind', choices=('anaphora', 'negative'), default='anaphora')
    args = parser.parse_args()

    key = Path(args.key_file).read_text(encoding='utf-8').strip()
    judge = DeepSeekJudge(key)
    cases = [case for case in build_cases() if case['category'] == args.kind]
    cfg = {'enabled': True, 'allowed_sender_ids': '42',
           'automatic_memory_enabled': True,
           'model_judge_enabled': True, 'judge_provider_id': 'deepseek'}
    rows = []
    with tempfile.TemporaryDirectory(prefix='g3-ab-') as root:
        runtime = sim.SimRuntime(root, cfg)
        runtime.plugin.context = judge
        await runtime.start()
        try:
            for index, case in enumerate(cases, 1):
                runner = run_case if args.kind == 'anaphora' else run_negative
                rows.append(await runner(runtime, judge, case, index))
        finally:
            await runtime.stop()

    if args.kind == 'anaphora':
        hits = sum(1 for row in rows if row['hit'])
        label = 'anaphora R@1'
    else:
        hits = sum(1 for row in rows if not row['injected'])
        label = 'negative 正确拒绝'
    report = {
        'suite': 'g3-ab-%s-v1' % args.kind,
        'note': '虚构语料；真实 DeepSeek 仅接收问题、最近原话与候选原话。',
        'cases': len(rows), 'hits': hits,
        'calls': len(judge.calls), 'tokens': judge.tokens,
        'avg_judge_latency_s': round(
            sum(call['latency_s'] for call in judge.calls) / max(1, len(judge.calls)), 3),
        'rows': rows,
    }
    out = Path(args.out) if args.out else (
        ROOT / 'reports' / ('g3_ab_anaphora.json' if args.kind == 'anaphora'
                            else 'g3_ab_negative.json'))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('%s: %d/%d' % (label, hits, len(rows)))
    print('judge calls:', len(judge.calls), 'tokens:', judge.tokens,
          'avg latency: %.3fs' % report['avg_judge_latency_s'])
    for row in rows:
        print('  %-12s %s lat=%.2fs chars=%d' % (
            row['id'], 'hit' if row.get('hit', not row['injected']) else 'miss',
            row['latency_s'], row['chars']))
    print('已写入', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
