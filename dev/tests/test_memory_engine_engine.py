import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestMemoryEngine(unittest.TestCase):
    def test_ingest_and_retrieve(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            receipt = engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "我喜欢喝乌龙茶"))
            self.assertTrue(receipt.receipt_ok)
            hits = engine.retrieve("s", "乌龙茶")
            self.assertEqual([h["event_id"] for h in hits], [receipt.event_id])
            context = engine.build_context("s", "乌龙茶", budget=100)
            self.assertEqual(len(context["items"]), 1)
            self.assertFalse(context["degraded"])
            store.close()

    def test_delete_source_removes_from_retrieval(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            receipt = engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "明天去成都"))
            self.assertEqual(len(engine.retrieve("s", "成都")), 1)
            self.assertGreaterEqual(engine.delete_source("s", 1), 1)
            self.assertEqual(engine.retrieve("s", "成都"), [])
            store.close()

    def test_context_budget_marks_degraded(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            for i in range(1, 4):
                engine.ingest("s", SourceEnvelope("s", i, "sha", "t", "茶" * 50))
            context = engine.build_context("s", "茶", budget=1)
            self.assertEqual(context["items"], [])
            self.assertTrue(context["degraded"])
            store.close()


    def test_projection_types(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            cases = [("我打算下周去青岛", "plan"), ("我喜欢乌龙茶", "preference"),
                     ("故事里他去了雪山", "story"), ("我昨天去了成都", "experience")]
            for index, (text, expected) in enumerate(cases, start=1):
                receipt = engine.ingest(
                    "s", SourceEnvelope("s", index, "sha", "t", text))
                items = store.memory_items_for_event(receipt.event_id)
                self.assertEqual(items[0]["item_type"], expected)
            store.close()


if __name__ == "__main__":
    unittest.main()
