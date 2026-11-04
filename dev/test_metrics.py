"""钩子耗时埋点：每轮写 latency.jsonl，标注是否注入；可关闭。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import sim_astrbot as sim


class TestLatencyMetrics(unittest.TestCase):
    def _run(self, cfg, texts, origin='webchat:FriendMessage:42'):
        async def go(root):
            runtime = sim.SimRuntime(root, cfg)
            await runtime.start()
            try:
                for index, text in enumerate(texts):
                    event = await runtime.receive(text, message_id='m%d' % index,
                                                  origin=origin)
                    await runtime.llm_request(event)
            finally:
                await runtime.stop()

        with tempfile.TemporaryDirectory() as root:
            asyncio.run(go(root))
            path = (Path(root) / 'plugin_data' / 'astrbot_plugin_digital_memory_store'
                    / 'latency.jsonl')
            content = path.read_text(encoding='utf-8') if path.exists() else ''
        return content.splitlines()

    def test_records_turns_and_injection_flag(self):
        lines = self._run(
            {'enabled': True, 'allowed_sender_ids': '42',
             'automatic_memory_enabled': True},
            ['我喜欢吃苹果', '喜欢吃苹果吗'])
        self.assertEqual(len(lines), 2, lines)
        rows = [json.loads(line) for line in lines]
        self.assertTrue(all(row['ms'] > 0 for row in rows))
        self.assertFalse(rows[0]['injected'], '第一轮没有可注入的历史')
        self.assertTrue(rows[1]['injected'], '第二轮应命中待审原话')

    def test_metrics_can_be_disabled(self):
        lines = self._run(
            {'enabled': True, 'allowed_sender_ids': '42',
             'automatic_memory_enabled': True, 'latency_metrics_enabled': False},
            ['我喜欢吃苹果', '喜欢吃苹果吗'])
        self.assertEqual(lines, [])

    def test_disabled_plugin_writes_nothing(self):
        lines = self._run({'enabled': False}, ['我喜欢吃苹果'])
        self.assertEqual(lines, [])


if __name__ == '__main__':
    unittest.main()
