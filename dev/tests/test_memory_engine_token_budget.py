import importlib.util
import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from memory_engine.token_budget import TokenCounter
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestExactTokenBudget(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("tiktoken"), "tiktoken not installed")
    def test_counter_counts_tokens(self):
        counter = TokenCounter("cl100k_base")
        self.assertEqual(counter.count("hello world"), 2)

    @unittest.skipUnless(importlib.util.find_spec("tiktoken"), "tiktoken not installed")
    def test_exact_budget_limits_items(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶" * 50))
            counter = TokenCounter("cl100k_base")
            packet = engine.build_context("s", "茶", budget_tokens=5,
                                          token_counter=counter)
            self.assertEqual(packet["items"], [])
            self.assertEqual(packet["used_tokens"], 0)
            self.assertEqual(packet["tokenizer"], "cl100k_base")
            store.close()


if __name__ == "__main__":
    unittest.main()
