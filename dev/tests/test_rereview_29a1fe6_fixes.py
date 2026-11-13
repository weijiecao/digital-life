"""Second-review R01/R02/R03 regressions.

R01: host budget follows the active provider, not the first config entry.
R02: engine backup verification requires the source chain.
R03: terminate must not block the host loop while a judge waits on it.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dlm_console.config import Paths
from dlm_console.core import Console
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
from memory_engine.engine import MemoryEngine
from tests.test_host_shadow_native import (FakeHostContext, load_host_shadow)
from tests.test_native_input_host import (Event, FakeConversation,
                                          ScriptedProvider)

MODULE = None


def setUpModule():
    global MODULE
    MODULE = load_host_shadow()


class HostCase(unittest.IsolatedAsyncioTestCase):
    env = {}

    async def asyncSetUp(self):
        from memory_engine.native_judge import NativeRelationJudge

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        (self.root / "data").mkdir(parents=True)
        values = {"ASTRBOT_ROOT": str(self.root), "SHADOW_PERSONA_ID": ""}
        values.update(self.env)
        patcher = patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = FakeConversation(
            "c1", [{"role": "user", "content": "周六去青岛海边散步。"}])
        self.context = FakeHostContext(self.conversation)
        judge = patch.object(
            MODULE.MemoryShadow, "_build_judge",
            lambda plugin, store: NativeRelationJudge(store, ScriptedProvider()))
        judge.start()
        self.addCleanup(judge.stop)
        self.plugin = MODULE.MemoryShadow(self.context, {})
        await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()


class ProviderBudgetTests(HostCase):
    """R01: config order and explicit limits must not override the active provider."""

    env = {"SHADOW_OUTPUT_MODE": "inject", "SHADOW_MEMORY_BUDGET": "512",
           "SHADOW_HOST_TOTAL_TOKENS": "1000000", "SHADOW_OUTPUT_TOKENS": "800"}

    def _request(self, **overrides):
        values = {"model": "small-model", "prompt": "问题", "system_prompt": "",
                  "contexts": [], "extra_user_content_parts": []}
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_active_provider_wins_over_first_config_entry(self):
        provider = SimpleNamespace(
            provider_config={"id": "small", "max_context_tokens": 1200})
        self.assertEqual(
            self.plugin.host_budget_for(self._request(), provider).total_tokens,
            1200)

    def test_active_provider_change_is_picked_up_per_request(self):
        large = SimpleNamespace(
            provider_config={"id": "large", "max_context_tokens": 1000000})
        small = SimpleNamespace(
            provider_config={"id": "small", "max_context_tokens": 1200})
        first = self.plugin.host_budget_for(self._request(), large).total_tokens
        second = self.plugin.host_budget_for(self._request(), small).total_tokens
        self.assertEqual((first, second), (1000000, 1200))

    def test_config_order_does_not_change_model_match(self):
        self.plugin.host_total_explicit = False
        request = self._request()
        self.plugin.provider_budgets = [
            ("large", frozenset(), 1000000),
            ("small", frozenset({"small-model"}), 1200),
        ]
        forward = self.plugin.host_budget_for(request).total_tokens
        self.plugin.provider_budgets.reverse()
        backward = self.plugin.host_budget_for(request).total_tokens
        self.assertEqual((forward, backward), (1200, 1200))

    def test_explicit_limit_is_a_cap_not_a_floor(self):
        self.plugin.host_total_explicit = True
        self.plugin.host_total = 900
        provider = SimpleNamespace(provider_config={"max_context_tokens": 1200})
        self.assertEqual(
            self.plugin.host_budget_for(self._request(), provider).total_tokens, 900)
        provider = SimpleNamespace(provider_config={"max_context_tokens": 600})
        self.assertEqual(
            self.plugin.host_budget_for(self._request(), provider).total_tokens, 600)

    def test_unknown_provider_uses_conservative_default(self):
        self.plugin.host_total_explicit = False
        self.plugin.host_total = 8192
        self.plugin.provider_budgets = []
        self.assertEqual(
            self.plugin.host_budget_for(self._request()).total_tokens, 8192)

    async def test_oversized_request_with_small_active_provider_stays_empty(self):
        self.context.get_using_provider = lambda *args, **kwargs: SimpleNamespace(
            provider_config={"id": "small", "max_context_tokens": 1200})
        await self.plugin.capture(Event("周六去青岛海边散步。", message_id="r1"))
        await self.plugin.pump.tick()
        request = SimpleNamespace(
            prompt="周六去青岛海边散步。", system_prompt="人格", contexts=[],
            extra_user_content_parts=[
                SimpleNamespace(type="text", text="长文本资料。" * 5000)])
        await self.plugin.observe(
            Event("周六去青岛海边散步。", message_id="r2"), request)
        record = json.loads(
            Path(self.plugin.log).read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["output_status"], "EMPTY")
        self.assertEqual(record["host_memory_tokens"], 0)
        self.assertEqual(len(request.extra_user_content_parts), 1)


class EngineSourceBackupTests(unittest.TestCase):
    """R02: a backup without the evidence source chain is not restorable."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "engine.sqlite3"
        self.store = V15Store(self.db)
        self.engine = MemoryEngine(self.store)
        text = "我喜欢喝乌龙茶。"
        self.engine.ingest(
            "s", SourceEnvelope("s", 1, hashlib.sha256(text.encode()).hexdigest(),
                                "2026-11-12", text),
            provenance={"narrator": "user", "subject": "user",
                        "reality_status": "real"})
        self.store.close()
        self.console = Console(Paths(workspace=self.root, instance=None,
                                     plugin_data=None, store_dir=None,
                                     shadow_dir=None, token="", read_only=False))

    def _verify(self):
        con = sqlite3.connect(self.db)
        try:
            return self.console._verify_backup_connection(con, "engine")
        finally:
            con.close()

    def test_valid_engine_backup_passes(self):
        report = self._verify()
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(report["checks"]["evidence_sources"])

    def test_missing_source_ref_table_is_rejected(self):
        con = sqlite3.connect(self.db)
        con.execute("DROP TABLE source_ref")
        con.commit()
        con.close()
        report = self._verify()
        self.assertFalse(report["ok"])
        self.assertIn("source_ref", " ".join(report["errors"]))

    def test_missing_source_row_is_rejected(self):
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM source_ref")
        con.commit()
        con.close()
        report = self._verify()
        self.assertFalse(report["ok"])
        self.assertIn("missing_source_ref", report["errors"])
        self.assertGreater(report["counts"]["evidence_without_source"], 0)

    def test_deleted_source_is_rejected(self):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE source_ref SET deleted_at='2026-11-13T00:00:00+00:00'")
        con.commit()
        con.close()
        report = self._verify()
        self.assertFalse(report["ok"])
        self.assertIn("missing_source_ref", report["errors"])


class ShutdownNonBlockingTests(unittest.IsolatedAsyncioTestCase):
    """R03: a waiting judge must not freeze the host loop during terminate."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir(parents=True)
        self.ready = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = []

        original = FakeHostContext

        class SlowContext(original):
            async def llm_generate(inner, chat_provider_id, prompt):
                self.ready.set()
                await self.release.wait()
                self.completed.append(True)
                return SimpleNamespace(completion_text='{"relations": []}')

        self.context = SlowContext(FakeConversation(
            "c1", [{"role": "user", "content": "周六去海边。"}]))
        values = {"ASTRBOT_ROOT": str(self.root), "SHADOW_PERSONA_ID": "",
                  "SHADOW_JUDGE_PROVIDER": "astrbot",
                  "SHADOW_JUDGE_PROVIDER_ID": "fake",
                  "SHADOW_JUDGE_TIMEOUT": "1"}
        patcher = patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plugin = MODULE.MemoryShadow(self.context, {})
        await self.plugin.initialize()
        await self.plugin.pump.stop()
        await self.plugin.capture(Event("周六去海边。"))
        await self.plugin.pump.stop()

    async def test_terminate_keeps_loop_responsive(self):
        tick = asyncio.create_task(self.plugin.pump.tick())
        self.plugin.pump.task = tick
        await asyncio.wait_for(self.ready.wait(), 2)
        fired = []

        async def timer():
            await asyncio.sleep(0.05)
            fired.append(time.monotonic())
            self.release.set()

        timer_task = asyncio.create_task(timer())
        start = time.monotonic()
        await asyncio.wait_for(self.plugin.terminate(), 3)
        elapsed = time.monotonic() - start
        await timer_task
        self.assertLess(elapsed, 0.8)
        self.assertLess(fired[0] - start, 0.8)
        self.assertTrue(self.completed)


if __name__ == "__main__":
    unittest.main()
