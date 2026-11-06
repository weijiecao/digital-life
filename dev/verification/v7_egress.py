"""V7：外发白名单（O8）。

用两种观测：
1. `socket.socket.connect` / `getaddrinfo` 记录任何网络连接；
2. 假 AstrBot context 记录 `llm_generate` 调用与入参。

三种配置：
- A 全关：只本地采集/检索，100 轮请求；
- B 仅本地 BGE：同上，模型已配置但只走本地编码；
- C 开窄路：候选筛选器显式开启，触发一次，检查只发问题/最近原话/候选。

产物：reports/verification_v7_egress.json
用法：python verification/v7_egress.py
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

import sim_astrbot as sim  # noqa: E402

MODEL_DIR = str(C.DEV.parent / 'testbed' / 'models' / 'bge-small-zh-v1.5')


class Recorder:
    def __init__(self):
        self.connects = []
        self.dns = []
        self.calls = []
        self._connect = socket.socket.connect
        self._getaddrinfo = socket.getaddrinfo

    def install(self):
        recorder = self

        def connect(sock, address):
            recorder.connects.append(repr(address))
            return recorder._connect(sock, address)

        def getaddrinfo(host, *args, **kwargs):
            recorder.dns.append(str(host))
            return recorder._getaddrinfo(host, *args, **kwargs)

        socket.socket.connect = connect
        socket.getaddrinfo = getaddrinfo

    def remove(self):
        socket.socket.connect = self._connect
        socket.getaddrinfo = self._getaddrinfo


class FakeContext:
    def __init__(self):
        self.calls = []

    async def llm_generate(self, chat_provider_id=None, prompt=None):
        self.calls.append({'provider': chat_provider_id, 'prompt': prompt})
        return types.SimpleNamespace(
            completion_text='{"choices":[1],"sufficient":true,'
                            '"conflict":false,"reason":"相关"}')


async def run_local(root, rounds=100):
    config = C.make_config([42], budget=600)
    runtime = sim.SimRuntime(root, config)
    await runtime.start()
    try:
        await runtime.receive('我喜欢吃苹果', message_id='seed-1')
        for _ in range(20):
            await runtime.drain_auto()
            if await runtime.jobs('pending') == 0:
                break
            await asyncio.sleep(0.01)
        for index in range(rounds):
            event = sim.PrivateEvent('我之前说过什么？', 'local-%d' % index)
            await runtime.llm_request(event)
        return await runtime.scalar('SELECT COUNT(*) FROM dl_capture')
    finally:
        await runtime.stop()


async def run_judge(root):
    config = C.make_config(
        [42], budget=600, model_dir='',
        automatic_memory_enabled=False, capture_enabled=True,
        auto_organize_enabled=True, context_enabled=True,
        always_context_enabled=False, pending_context_enabled=False,
        followup_enabled=False, auto_group_context_enabled=False,
        auto_gap_recovery_enabled=False,
        model_judge_enabled=True,
        judge_provider_id='fake-provider', judge_hourly_limit=100)
    runtime = sim.SimRuntime(root, config)
    runtime.plugin.context = FakeContext()
    await runtime.start()
    try:
        for index, text in enumerate(['原计划周六去书店', '后来改到周日', '再后来取消了'], 1):
            await runtime.receive(text, message_id='seed-%d' % index)
        for _ in range(40):
            await runtime.drain_auto()
            if await runtime.jobs('pending') == 0:
                break
            await asyncio.sleep(0.01)
        event = sim.PrivateEvent('后来呢？', 'judge-1')
        injection = await runtime.llm_request(event)
        return {'calls': runtime.plugin.context.calls,
                'parts': injection.count,
                'prefix': injection.text.startswith('[DL_MEMORY_V040]'),
                'text': injection.text[:800]}
    finally:
        await runtime.stop()


def _is_loopback(address):
    return any(marker in address for marker in ("'127.0.0.1'", "'::1'", "'localhost'"))


def main():
    recorder = Recorder()
    recorder.install()
    try:
        with C.TempRoot(prefix='dl-v7-local-') as root:
            captures = asyncio.run(run_local(root))
        external = [item for item in recorder.connects if not _is_loopback(item)]
        loopback = [item for item in recorder.connects if _is_loopback(item)]
        with C.TempRoot(prefix='dl-v7-judge-') as root:
            judge = asyncio.run(run_judge(root))
    finally:
        recorder.remove()

    calls = judge['calls']
    prompt = calls[0]['prompt'] if calls else ''
    prompt_checks = {
        'one_call': len(calls) == 1,
        'provider': bool(calls) and calls[0]['provider'] == 'fake-provider',
        'has_query': '后来呢' in prompt,
        'has_candidates': '原计划周六去书店' in prompt,
        'no_scope_hash': 'manual-test:' not in prompt,
        'no_abs_path': 'C:\\' not in prompt and '/testbed/' not in prompt,
        'full_db_absent': '我喜欢吃苹果' not in prompt,
    }
    local_checks = {
        'captures': captures == 1,
        'zero_external_connect': not external,
        'zero_dns': not recorder.dns,
    }
    payload = {
        'suite': 'v7-egress',
        'local': {'rounds': 100, 'checks': local_checks,
                  'external_connects': external, 'loopback_connects': loopback,
                  'dns': recorder.dns},
        'judge': {'calls': len(calls), 'checks': prompt_checks,
                  'parts': judge['parts'], 'prefix': judge['prefix'],
                  'prompt_excerpt': prompt[:600]},
        'pass': all(local_checks.values()) and all(prompt_checks.values()),
    }
    out = C.DEV / 'reports' / 'verification_v7_egress.json'
    C.dump_json(out, payload)
    print(json.dumps({'local': local_checks, 'judge': prompt_checks,
                      'pass': payload['pass'], 'report': str(out)},
                     ensure_ascii=False, indent=2))
    return 0 if payload['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
