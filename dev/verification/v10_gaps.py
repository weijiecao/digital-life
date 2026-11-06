"""V10：采集缺口可见性。

核对：媒体/混合消息、超长消息、越权来源、群聊各自留下什么痕迹。
媒体/混合消息不计入 gap，但必须计入独立的 capture_unsupported 计数器。

产物：reports/verification_v10_gaps.json
用法：python verification/v10_gaps.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

import sim_astrbot as sim  # noqa: E402


async def run(root):
    runtime = sim.SimRuntime(root, C.make_config([42], budget=600))
    await runtime.start()
    try:
        await runtime.receive('正常文本', message_id='ok-1')
        await runtime.receive('带图片', message_id='media-1',
                              parts=[sim.Plain('带图片'), sim.Image()])
        await runtime.receive('纯图片', message_id='media-2', parts=[sim.Image()])
        await runtime.receive('长' * 4001, message_id='long-1')
        await runtime.receive('陌生人', message_id='stranger-1', sender='999')
        await runtime.receive('群聊', message_id='group-1', group_id='10001')
        await runtime.receive('/dlm 帮助', message_id='command-1')
        rows = await runtime.scalar('SELECT COUNT(*) FROM dl_capture')
        gaps = await runtime.scalar('SELECT COALESCE(SUM(total),0) FROM dl_capture_gaps')
        unresolved = await runtime.scalar('SELECT COALESCE(SUM(unresolved),0) FROM dl_capture_gaps')
        skipped = runtime.plugin.capture_skipped
        unsupported = runtime.plugin.capture_unsupported
        return {'captures': rows, 'gaps_total': gaps, 'gaps_unresolved': unresolved,
                'capture_skipped': skipped, 'capture_unsupported': unsupported}
    finally:
        await runtime.stop()


def main():
    with C.TempRoot(prefix='dl-v10-') as root:
        facts = asyncio.run(run(root))
    checks = {
        'normal_captured': facts['captures'] == 1,
        'oversize_gap': facts['gaps_total'] >= 1,
        'media_unsupported_visible': facts['capture_unsupported'] >= 2,
        'skipped_counter_covers_failures': facts['capture_skipped'] >= 1,
    }
    payload = {'suite': 'v10-gaps', 'facts': facts, 'checks': checks,
               'pass': all(checks.values()),
               'finding': ('媒体/混合消息没有进入 capture_unsupported，'
                           '无法从现有数据回答“漏了什么”。'
                           if not checks['media_unsupported_visible'] else None)}
    out = C.DEV / 'reports' / 'verification_v10_gaps.json'
    C.dump_json(out, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
