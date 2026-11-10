import importlib.util
import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from memory_engine.host_budget import HostBudget
from memory_engine.token_budget import TokenCounter
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestHostBudget(unittest.TestCase):
    def test_memory_budget_subtracts_reserves(self):
        self.assertEqual(HostBudget(100, 20, 30, 10).memory_tokens, 40)
        self.assertEqual(HostBudget(10, 20, 0, 0).memory_tokens, 0)

    @unittest.skipUnless(importlib.util.find_spec("tiktoken"), "tiktoken not installed")
    def test_rendered_context_uses_host_budget(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t",
                                              "我喜欢乌龙茶。" * 2))
            packet = engine.build_rendered_context(
                "s", "乌龙茶", 999, TokenCounter(),
                host_budget=HostBudget(120, 40, 20, 20))
            self.assertLessEqual(packet["rendered_tokens"], 40)
            self.assertTrue(packet["budget_enforced"])
            store.close()


if __name__ == "__main__":
    unittest.main()
