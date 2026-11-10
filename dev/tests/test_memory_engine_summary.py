import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from memory_engine.summary import summarize
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestSummary(unittest.TestCase):
    def test_summary_is_bounded_and_keeps_negation(self):
        text = "我不喜欢喝浓茶。我喜欢乌龙茶，但只在下午喝。"
        summary = summarize(text, max_chars=20)
        self.assertLessEqual(len(summary), 20)
        self.assertIn("不", summary)

    def test_context_uses_summary(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            long_text = "我喜欢乌龙茶。" * 50
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", long_text))
            packet = engine.build_context("s", "乌龙茶", budget=600)
            self.assertEqual(len(packet["items"]), 1)
            self.assertLessEqual(len(packet["items"][0]["summary"]), 120)
            self.assertGreaterEqual(len(packet["items"][0]["evidence"]), 100)
            store.close()


if __name__ == "__main__":
    unittest.main()
