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


class LenCounter:
    def count(self, text):
        return len(text)


class TestRenderedBudget(unittest.TestCase):
    def _engine(self, d):
        store = V15Store(str(Path(d) / "v15.sqlite3"))
        engine = MemoryEngine(store)
        engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "tea " * 12))
        return store, engine

    @needs_tiktoken
    def test_final_block_fits_exact_budget(self):
        with tempfile.TemporaryDirectory() as d:
            store, engine = self._engine(d)
            counter = TokenCounter()
            packet = engine.build_rendered_context("s", "tea", 150, counter)
            self.assertTrue(packet["budget_enforced"])
            self.assertLessEqual(packet["rendered_tokens"], 150)
            self.assertTrue(packet["items"])
            evidence = packet["items"][0]["evidence"]
            self.assertIn(evidence, packet["rendered"])
            self.assertEqual(packet["rendered"].count(evidence), 1)
            self.assertEqual(packet["items"][0]["admission"]["verdict"], "SUPPORTED")
            store.close()

    def test_missing_tokenizer_is_strict_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            store, engine = self._engine(d)
            packet = engine.build_rendered_context("s", "tea", 40, None)
            self.assertTrue(packet["estimated"])
            self.assertTrue(packet["budget_enforced"])
            self.assertIsNone(packet["rendered_tokens"])
            self.assertEqual(packet["rendered"], "")
            self.assertEqual(packet["items"], [])
            self.assertIn("tokenizer_missing", packet["degraded_reasons"])
            store.close()

    def test_zero_budget_emits_empty_block(self):
        with tempfile.TemporaryDirectory() as d:
            store, engine = self._engine(d)
            packet = engine.build_rendered_context("s", "tea", 0, LenCounter())
            self.assertEqual(packet["items"], [])
            self.assertEqual(packet["source_refs"], [])
            self.assertEqual(packet["rendered"], "")
            self.assertEqual(packet["rendered_tokens"], 0)
            self.assertTrue(packet["budget_enforced"])
            self.assertIn("zero_budget", packet["degraded_reasons"])
            store.close()

    def test_fit_packet_rebuilds_refs_after_trim(self):
        from memory_engine.render import fit_packet, render_context
        packet = {"scope_id": "s", "query": "q", "items": [
            {"event_id": "e1", "title": "t1", "summary": "a", "evidence": "a"},
            {"event_id": "e2", "title": "t2", "summary": "b", "evidence": "b"},
        ], "source_refs": [{"event_id": "e1"}, {"event_id": "e2"}]}
        one = dict(packet, items=[packet["items"][0]])
        budget = len(render_context(one))
        result = fit_packet(packet, LenCounter(), budget)
        self.assertEqual([item["event_id"] for item in result["items"]], ["e1"])
        self.assertEqual(result["source_refs"], [{"event_id": "e1"}])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["rendered_tokens"], budget)

    def test_fit_packet_frame_overflow_is_empty(self):
        from memory_engine.render import fit_packet
        packet = {"scope_id": "s", "query": "q", "items": [
            {"event_id": "e1", "title": "t1", "summary": "a", "evidence": "a"}]}
        result = fit_packet(packet, LenCounter(), 1)
        self.assertEqual(result["rendered"], "")
        self.assertEqual(result["items"], [])
        self.assertTrue(result["budget_enforced"])
        self.assertIn("frame_exceeds_budget", result["degraded_reasons"])


if __name__ == "__main__":
    unittest.main()
