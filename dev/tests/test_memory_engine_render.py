import importlib.util
import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from memory_engine.token_budget import TokenCounter
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store

needs_tiktoken = unittest.skipUnless(
    importlib.util.find_spec("tiktoken"), "tiktoken not installed")


class TestRenderedBudget(unittest.TestCase):
    def _engine(self, d):
        store = V15Store(str(Path(d) / "v15.sqlite3"))
        engine = MemoryEngine(store)
        engine.ingest("s", SourceEnvelope("s", 1, "sha", "t",
                                          "我喜欢乌龙茶。" * 2))
        return store, engine

    @needs_tiktoken
    def test_final_block_fits_exact_budget(self):
        with tempfile.TemporaryDirectory() as d:
            store, engine = self._engine(d)
            counter = TokenCounter()
            packet = engine.build_rendered_context("s", "乌龙茶", 150, counter)
            self.assertTrue(packet["budget_enforced"])
            self.assertLessEqual(packet["rendered_tokens"], 150)
            self.assertIn("summary:", packet["rendered"])
            self.assertIn("evidence:", packet["rendered"])
            store.close()

    def test_missing_tokenizer_is_estimated_not_hard(self):
        with tempfile.TemporaryDirectory() as d:
            store, engine = self._engine(d)
            packet = engine.build_rendered_context("s", "乌龙茶", 40, None)
            self.assertTrue(packet["estimated"])
            self.assertFalse(packet["budget_enforced"])
            self.assertIsNone(packet["rendered_tokens"])
            store.close()


if __name__ == "__main__":
    unittest.main()
