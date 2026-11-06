"""①→⑤ 离线端到端探针：在可观测假 AstrBot 上跑通"采集→整理→注入→重启"。

这是 M1 的 L1 层（本机可观测集成），产出 `reports/integration_chain.json`。
它回答四个可复跑的问题：
  1. 钩子是否被框架调用、入参字段是否齐全（contract 段）
  2. 私聊纯文本是否真的落库、非文本链与越权来源是否被拒（capture 段）
  3. 注入是否生效、是否遵守预算与临时片段约定（injection 段）
  4. 后台任务在重启后是否续跑、是否重复采集/重复注入（restart 段）

**边界**：编码器是受控替身、平台是替身，因此这里的召回率与延迟只代表
"管线通不通"，不代表真实 BGE / AstrBot / QQ 上的准确率（见 METRICS.md）。

用法：python integration_probe.py
"""

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

import sim_astrbot as sim

ROOT = Path(__file__).resolve().parent
CONFIG = {'enabled': True, 'allowed_sender_ids': '42',
          'automatic_memory_enabled': True, 'context_budget_chars': 450}
SEED_TEXT = '我喜欢吃苹果'
QUERY = '你之前喜欢吃苹果吗'
ROUNDS = 8


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _capture_rows(runtime):
    return runtime.scalar('SELECT COUNT(*) FROM dl_capture')


def _gaps(runtime):
    return runtime.scalar('SELECT COALESCE(SUM(total),0) FROM dl_capture_gaps')


async def seed(runtime):
    await runtime.receive(SEED_TEXT, message_id='seed-1')
    # 模拟"先聊、后问"：把原话时间改到过去，避免与本次请求同一秒。
    await runtime.execute(
        "UPDATE dl_capture SET recorded_at='2025-01-01T00:00:00+00:00' WHERE seq=1")
    await runtime.drain_auto()


async def _wait_jobs_drained(runtime, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await runtime.jobs('pending') == 0:
            return True
        await asyncio.sleep(0.1)
    return False


async def run_probe(root, rounds=ROUNDS):
    facts = {'suite': 'astrbot-integration-probe-v1', 'rounds': rounds,
             'contract': sim.report_contract()['hooks']}
    runtime = sim.SimRuntime(root, CONFIG)
    await runtime.start()
    try:
        # --- capture：纯文本落库；非文本链与越权来源必须被拒 ---
        await seed(runtime)
        await runtime.receive('看这个', message_id='media-1',
                              parts=[sim.Plain('看这个'), sim.Image()])
        await runtime.receive(SEED_TEXT, message_id='stranger-1', sender='999')
        await runtime.receive(SEED_TEXT, message_id='group-1', group_id='10001')
        await runtime.receive(SEED_TEXT, message_id='origin-1',
                              origin='test:GroupMessage:10001')
        rows = await _capture_rows(runtime)
        facts['capture'] = {
            'rows': rows,
            'media_chain_skipped': rows == 1,
            'stranger_ignored': rows == 1,
            'group_ignored': rows == 1,
            'gap_records': await _gaps(runtime),
            'unsupported': runtime.plugin.capture_unsupported,
        }

        # --- injection：8 轮真实钩子调用，记录耗时与预算；首轮顺带验证"同一请求不重复注入" ---
        latencies, injected, budget_ok, temporary = [], 0, True, True
        dedupe = None
        for index in range(rounds):
            event = await runtime.receive(QUERY, message_id='query-%d' % index)
            if index == 0:
                request = sim.LlmRequest('v4293')
                first = await runtime.llm_request(event, request=request)
                second = await runtime.llm_request(event, request=request)
                dedupe = {'first_parts': first.count, 'second_parts': second.count,
                          'stable': first.count == second.count}
                result = first
            else:
                result = await runtime.llm_request(event)
            latencies.append(result.elapsed)
            if result.is_memory:
                injected += 1
                budget_ok = budget_ok and len(result.body()) <= CONFIG['context_budget_chars']
                temporary = temporary and result.temporary
        facts['injection'] = {
            'rounds': rounds,
            'injected': injected,
            'rate': round(injected / rounds, 3),
            'prefix_present': injected > 0,
            'temporary_parts': temporary,
            'budget_respected': budget_ok,
        }
        facts['latency_ms'] = {
            'samples': [round(x * 1000, 2) for x in latencies],
            'p50': round(percentile(latencies, 0.50) * 1000, 2),
            'p95': round(percentile(latencies, 0.95) * 1000, 2),
            'max': round(max(latencies) * 1000, 2),
            'mean': round(statistics.fmean(latencies) * 1000, 2),
        }

        facts['dedupe'] = dedupe

        rows_before = await _capture_rows(runtime)
        pending_before = await runtime.jobs('pending')
    finally:
        await runtime.stop()

    # --- restart：同一数据目录重启，积压任务续跑且不重复采集 ---
    restarted = sim.SimRuntime(root, CONFIG)
    await restarted.start()
    try:
        drained = await _wait_jobs_drained(restarted)
        pending_after_drain = await restarted.jobs('pending')
        rows_after = await _capture_rows(restarted)
        await restarted.receive(SEED_TEXT, message_id='seed-1')
        rows_after_dup = await _capture_rows(restarted)
        event = await restarted.receive(QUERY, message_id='after-restart-1')
        result = await restarted.llm_request(event)
        facts['restart'] = {
            'pending_before_restart': pending_before,
            'jobs_drained_after_restart': drained,
            'pending_after_drain': pending_after_drain,
            'capture_rows_before': rows_before,
            'capture_rows_after': rows_after,
            'duplicate_capture': rows_after_dup != rows_after,
            'injection_after_restart': result.count,
        }
    finally:
        await restarted.stop()

    # --- legacy：宿主没有 extra_user_content_parts 时必须静默放行 ---
    with tempfile.TemporaryDirectory(prefix='dl-legacy-') as legacy_root:
        legacy = sim.SimRuntime(legacy_root, CONFIG, profile='legacy')
        await legacy.start()
        try:
            await seed(legacy)
            event = await legacy.receive(QUERY, message_id='legacy-1')
            raised, parts = False, 0
            try:
                parts = (await legacy.llm_request(event)).count
            except Exception:
                raised = True
            facts['legacy_host'] = {'raised': raised, 'parts': parts,
                                    'failed_open': not raised and parts == 0}
        finally:
            await legacy.stop()

    facts['limitations'] = ('替身平台与受控编码器；只证明链路与契约自洽，'
                            '不代表真实 AstrBot / BGE / QQ 上的准确率或延迟。')
    return facts


def main():
    with tempfile.TemporaryDirectory(prefix='dl-probe-') as root:
        facts = asyncio.run(run_probe(root))
    out = ROOT / 'reports'
    out.mkdir(exist_ok=True)
    (out / 'integration_chain.json').write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')

    print('钩子契约：')
    for hook in facts['contract']:
        print('  %-22s %-20s %s' % (hook['method'], ','.join(hook['kinds']),
                                    '(' + ', '.join(hook['parameters']) + ')'))
    print('采集：%d 条；非文本链跳过=%s；越权来源拒绝=%s' % (
        facts['capture']['rows'], facts['capture']['media_chain_skipped'],
        facts['capture']['stranger_ignored']))
    print('注入：%d/%d 轮；临时片段=%s；预算合规=%s' % (
        facts['injection']['injected'], facts['injection']['rounds'],
        facts['injection']['temporary_parts'], facts['injection']['budget_respected']))
    print('钩子耗时：P50=%.1fms P95=%.1fms max=%.1fms' % (
        facts['latency_ms']['p50'], facts['latency_ms']['p95'], facts['latency_ms']['max']))
    print('去重：两次调用片段数 %d/%d' % (
        facts['dedupe']['first_parts'], facts['dedupe']['second_parts']))
    print('重启：积压 %d → 待处理 %d；重复采集=%s；重启后注入=%d' % (
        facts['restart']['pending_before_restart'], facts['restart']['pending_after_drain'],
        facts['restart']['duplicate_capture'], facts['restart']['injection_after_restart']))
    print('不兼容宿主：静默放行=%s' % facts['legacy_host']['failed_open'])
    print('\n已写入 reports/integration_chain.json（替身环境，非实机证据）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
