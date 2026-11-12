"""host_shadow 输出注入接线：显式开启后，模型能看到临时记忆块，prompt 与历史不被改写。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from memory_engine.native_judge import NativeRelationJudge
from tests.test_native_input_host import (Event, FakeConversation, FakeManager,
                                          ScriptedProvider)
from tests.test_host_shadow_native import load_host_shadow

MODULE = None


def setUpModule():
    global MODULE
    MODULE = load_host_shadow()


class FakeHostContext:
    def __init__(self, conversation):
        self.conversation_manager = FakeManager(conversation)


class HostShadowOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        (self.root / "data").mkdir(parents=True)
        os.environ["ASTRBOT_ROOT"] = str(self.root)
        self.conversation = FakeConversation("c1", [
            {"role": "user", "content": "周六去青岛海边散步。"},
            {"role": "assistant", "content": "记得看天气。"},
            {"role": "user", "content": "青岛海边的栈桥也去了。"},
        ])
        self.context = FakeHostContext(self.conversation)
        patcher = patch.object(
            MODULE.MemoryShadow, "_build_judge",
            lambda plugin, store: NativeRelationJudge(store, ScriptedProvider()))
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch.dict(os.environ, {"SHADOW_OUTPUT_MODE": "inject",
                                     "SHADOW_MEMORY_BUDGET": "512"}):
            self.plugin = MODULE.MemoryShadow(self.context, {})
        await self.plugin.initialize()
        # 使用 sim_astrbot 提供的 TextPart（带 _no_save），走默认 AstrBot 适配器。

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def _read_log(self):
        path = Path(self.plugin.log)
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    async def test_inject_temporary_memory_without_mutating_request(self):
        await self.plugin.capture(Event("青岛海边的栈桥也去了。", message_id="m3"))
        await self.plugin.pump.tick()
        request = SimpleNamespace(
            prompt="青岛海边的栈桥也去了。", system_prompt="保留原人格",
            contexts=[{"role": "user", "content": "原始历史"}],
            extra_user_content_parts=[])
        before = (request.prompt, request.system_prompt, list(request.contexts))
        await self.plugin.observe(
            Event("青岛海边的栈桥也去了。", message_id="m4"), request)

        parts = request.extra_user_content_parts
        self.assertEqual(len(parts), 1)
        self.assertTrue(parts[0]._no_save)
        self.assertIn("<memory_evidence>", parts[0].text)
        self.assertEqual(before, (request.prompt, request.system_prompt,
                                  list(request.contexts)))
        records = await self._read_log()
        self.assertEqual(records[-1]["output_status"], "INJECTED")
        self.assertGreaterEqual(records[-1]["items"], 1)
        self.assertGreater(records[-1]["rendered_tokens"], 0)

        # 同一请求重复观察只保留一个临时块；新请求 ID 允许更新内容。
        await self.plugin.observe(
            Event("青岛海边的栈桥也去了。", message_id="m4"), request)
        self.assertEqual(len(request.extra_user_content_parts), 1)
        self.assertIn((await self._read_log())[-1]["output_status"],
                      ("INJECTED", "UNCHANGED"))

    async def test_default_off_never_injects(self):
        with patch.dict(os.environ, {"SHADOW_OUTPUT_MODE": "off"}):
            plugin = MODULE.MemoryShadow(self.context, {})
        await plugin.initialize()
        try:
            request = SimpleNamespace(
                prompt="青岛海边的栈桥也去了。", system_prompt="system",
                contexts=[], extra_user_content_parts=[])
            await plugin.observe(
                Event("青岛海边的栈桥也去了。", message_id="m5"), request)
            self.assertEqual(request.extra_user_content_parts, [])
        finally:
            await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
