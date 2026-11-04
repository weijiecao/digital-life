"""每轮上下文供给：没有直接匹配时也要给有界背景包（未确认）。"""

import asyncio
import tempfile
import unittest

import sim_astrbot as sim
from test_store import FakeEncoder

from astrbot_plugin_digital_memory_store.storage import Store


class TestBackgroundContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, '', FakeEncoder)
        self.store.always_context_enabled = True
        self.scope = self.store.scope('1', 's')
        self.store.capture(self.scope, 'm-a', '我喜欢吃苹果',
                           recorded_at='2025-01-01T10:00:00+00:00')
        self.store.capture(self.scope, 'm-b', '周末去公园跑步',
                           recorded_at='2025-01-01T10:01:00+00:00')
        self.store.capture(self.scope, 'm-c', '今天天气不错',
                           recorded_at='2025-01-01T10:02:00+00:00')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_background_packet_when_no_match(self):
        packet = self.store.context_packet(self.scope, '火星飞船怎么租？', budget=600,
                                           current_message_id='m-c')
        self.assertIn('背景上下文', packet)
        self.assertIn('我喜欢吃苹果', packet)
        self.assertIn('未确认', packet)
        self.assertNotIn('今天天气不错', packet, '当前消息必须排除')
        self.assertLessEqual(len(packet), 320)

    def test_disabled_returns_empty(self):
        self.store.always_context_enabled = False
        packet = self.store.context_packet(self.scope, '火星飞船怎么租？', budget=600,
                                           current_message_id='m-c')
        self.assertEqual(packet, '')

    def test_empty_history_returns_empty(self):
        empty_scope = self.store.scope('2', 's')
        packet = self.store.context_packet(empty_scope, '火星飞船怎么租？', budget=600,
                                           current_message_id='m-x')
        self.assertEqual(packet, '')


class TestPluginAlwaysContext(unittest.TestCase):
    def _run(self, cfg, texts):
        async def go(root):
            runtime = sim.SimRuntime(root, cfg)
            await runtime.start()
            parts = []
            try:
                for index, text in enumerate(texts):
                    event = await runtime.receive(text, message_id='m%d' % index,
                                                  origin='webchat:FriendMessage:42')
                    result = await runtime.llm_request(event)
                    parts.append(result.text)
            finally:
                await runtime.stop()
            return parts

        with tempfile.TemporaryDirectory() as root:
            return asyncio.run(go(root))

    def test_unrelated_turn_still_gets_background(self):
        parts = self._run(
            {'enabled': True, 'allowed_sender_ids': '42',
             'automatic_memory_enabled': True, 'always_context_enabled': True},
            ['我喜欢吃苹果', '火星飞船怎么租？'])
        self.assertIn('背景上下文', parts[1])
        self.assertIn('我喜欢吃苹果', parts[1])
        self.assertIn('未确认', parts[1])

    def test_followup_without_focus_still_gets_context(self):
        parts = self._run(
            {'enabled': True, 'allowed_sender_ids': '42',
             'automatic_memory_enabled': True, 'always_context_enabled': True},
            ['我喜欢吃苹果', '喜欢吃苹果吗', '后来呢？'])
        self.assertTrue(parts[2], '指代分支没接住时也必须给背景上下文')
        self.assertIn('未确认', parts[2])

    def test_disabled_skips_background(self):
        parts = self._run(
            {'enabled': True, 'allowed_sender_ids': '42',
             'automatic_memory_enabled': True, 'always_context_enabled': False},
            ['我喜欢吃苹果', '火星飞船怎么租？'])
        self.assertEqual(parts[1], '')


if __name__ == '__main__':
    unittest.main()
