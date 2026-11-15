"""host_shadow 的原生输入接线：AstrBot 替身 → 会话历史 → 判别 → 引擎检索。

证明「插件生命周期里自动读取历史、去重、判别、可检索」这条线路接通；
不证明真实 AstrBot/NapCat 或真实模型效果。
"""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sim_astrbot as sim
from tests.test_native_input_host import (Event, FakeConversation, FakeManager,
                                          ScriptedProvider)
from memory_engine.native_judge import NativeRelationJudge

DEV = Path(__file__).resolve().parents[1]
HOST_SHADOW = 'host_shadow.main'


class FakeHostContext:
    def __init__(self, conversation):
        self.conversation_manager = FakeManager(conversation)
        self.prompts = []

    async def llm_generate(self, chat_provider_id, prompt):
        self.prompts.append((chat_provider_id, prompt))
        return SimpleNamespace(completion_text='{"relations": []}')


class LegacyHostContext:
    """没有 conversation_manager 的宿主：只能回退到单条消息。"""

    def __init__(self):
        self.prompts = []

    async def llm_generate(self, chat_provider_id, prompt):
        self.prompts.append((chat_provider_id, prompt))
        return SimpleNamespace(completion_text='{"relations": []}')


def load_host_shadow():
    """在 AstrBot 替身环境里导入 shadow 插件；DEV 必须在导入前设置。"""
    os.environ['DIGITAL_LIFE_DEV'] = str(DEV)
    sys.modules.update(sim._build_stubs(sim.Registry(), {'data_path': '.'},
                                       'v4293'))
    sys.modules.pop(HOST_SHADOW, None)
    return importlib.import_module(HOST_SHADOW)


MODULE = None


def setUpModule():
    global MODULE
    MODULE = load_host_shadow()


class HostShadowNativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'root'
        (self.root / 'data').mkdir(parents=True)
        os.environ['ASTRBOT_ROOT'] = str(self.root)
        self.module = MODULE
        self.conversation = FakeConversation('c1', [
            {'role': 'user', 'content': '周六去青岛海边散步。'},
            {'role': 'assistant', 'content': '记得看天气。'},
            {'role': 'user', 'content': '青岛海边的栈桥也去了。'},
        ])
        self.context = FakeHostContext(self.conversation)
        patcher = patch.object(
            self.module.MemoryShadow, '_build_judge',
            lambda plugin, store: NativeRelationJudge(store, ScriptedProvider()))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plugin = self.module.MemoryShadow(self.context, {})
        await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def _read(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.plugin.pool,
            lambda: self.plugin.store.db.execute(sql, params).fetchall())

    async def test_capture_reads_history_and_creates_events(self):
        await self.plugin.capture(Event('青岛海边的栈桥也去了。', message_id='m3'))
        self.assertEqual(self.plugin.input_modes['conversation'], 1)
        self.assertEqual(self.plugin.input_modes['skipped'], 0)
        counts = await self.plugin.pump.tick()
        self.assertEqual(counts['processed'], 2)  # 两条用户原话
        rows = await self._read("SELECT COUNT(*) FROM native_input_inbox")
        self.assertEqual(rows[0][0], 3)  # 助手消息只归档
        events = await self._read("SELECT COUNT(*) FROM event_candidate")
        self.assertEqual(events[0][0], 2)
        # 同一份历史再采集一次不新增记录。
        await self.plugin.capture(Event('青岛海边的栈桥也去了。', message_id='m3'))
        self.assertEqual((await self._read("SELECT COUNT(*) FROM native_input_inbox"))[0][0], 3)

    async def test_observe_writes_one_record_with_memory(self):
        await self.plugin.capture(Event('青岛海边的栈桥也去了。', message_id='m3'))
        await self.plugin.pump.tick()
        request = SimpleNamespace(contexts=[{'role': 'user', 'content': '之前说过去哪？'}],
                                  system_prompt='system')
        await self.plugin.observe(Event('青岛海边的栈桥也去了。', message_id='m4'), request)
        lines = (Path(self.plugin.log)).read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertGreaterEqual(record['items'], 1)
        self.assertEqual(record['query'], '青岛海边的栈桥也去了。')
        self.assertTrue(record['scope'].startswith('native-'))

    async def test_no_conversation_skips_without_guessing(self):
        empty = self.module.MemoryShadow(LegacyHostContext(), {})
        await empty.initialize()
        try:
            await empty.capture(Event('孤立消息', message_id='m9'))
            # 没有会话管理器时回退到单条消息（带稳定 ID），不伪造历史。
            self.assertEqual(empty.input_modes['event'], 1)
            result = await empty.pump.tick()
            self.assertEqual(result['created'], 1)
        finally:
            await empty.terminate()

    async def test_group_and_unknown_senders_are_skipped(self):
        await self.plugin.capture(Event('群消息', group_id='123', message_id='g1'))
        await self.plugin.capture(Event('无发送者', user=None, message_id='g2'))
        self.assertEqual(self.plugin.input_modes['skipped'], 2)
        self.assertEqual(self.plugin.input_modes['conversation'], 0)


if __name__ == '__main__':
    unittest.main()
