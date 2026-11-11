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


    def test_context_cache_hits_and_invalidates(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            first = engine.build_context("s", "茶")
            second = engine.build_context("s", "茶")
            self.assertIs(first, second)
            self.assertEqual(engine.cache.size(), 1)
            engine.ingest("s", SourceEnvelope("s", 2, "sha", "t", "茶点"))
            third = engine.build_context("s", "茶")
            self.assertIsNot(first, third)
            self.assertEqual(len(third["items"]), 2)
            engine.delete_source("s", 1)
            fourth = engine.build_context("s", "茶")
            self.assertEqual(len(fourth["items"]), 1)
            store.close()


    def test_ingest_is_source_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            source = SourceEnvelope("s", 1, "sha", "t", "重复消息")
            first = engine.ingest("s", source)
            second = engine.ingest("s", source)
            self.assertEqual(first.event_id, second.event_id)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0], 1)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0], 1)
            store.close()


    def test_token_budget_is_conservative(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶" * 40))
            packet = engine.build_context("s", "茶", budget_tokens=1)
            self.assertEqual(packet["items"], [])
            self.assertEqual(packet["used_tokens"], 0)
            packet = engine.build_context("s", "茶", budget_tokens=100)
            self.assertEqual(len(packet["items"]), 1)
            self.assertGreater(packet["used_tokens"], 0)
            store.close()


    def test_revise_and_withdraw(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            old = engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "住在杭州"))
            new_event = engine.revise_source(
                "s", 1, SourceEnvelope("s", 2, "sha", "t", "搬到上海"))
            self.assertEqual(engine.retrieve("s", "杭州"), [])
            self.assertEqual([h["event_id"] for h in engine.retrieve("s", "上海")],
                             [new_event])
            self.assertTrue(engine.withdraw_event("s", new_event))
            self.assertEqual(engine.retrieve("s", "上海"), [])
            self.assertEqual(store.events_for_source("s", 1), [])
            store.close()


    def test_retrieve_hides_pending_source(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            store.save_pending("s", 1, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": ["e1", "e2"]}, "split")
            self.assertEqual(engine.retrieve("s", "茶"), [])
            self.assertEqual(engine.build_context("s", "茶")["items"], [])
            store.close()


    def test_other_connection_deletion_invalidates_context_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            a = V15Store(path)
            b = V15Store(path)
            engine_a = MemoryEngine(a)
            engine_b = MemoryEngine(b)
            engine_a.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            self.assertEqual(len(engine_a.build_context("s", "茶")["items"]), 1)
            engine_b.delete_source("s", 1)
            self.assertEqual(engine_a.build_context("s", "茶")["items"], [])
            a.close()
            b.close()


    def test_other_connection_deletion_invalidates_hybrid_index(self):
        class Encoder:
            def encode(self, texts):
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            a = V15Store(path)
            b = V15Store(path)
            engine_a = MemoryEngine(a, encoder=Encoder())
            engine_b = MemoryEngine(b)
            engine_a.ingest("s", SourceEnvelope("s", 1, "sha", "t", "杭州"))
            self.assertEqual(len(engine_a.retrieve("s", "杭州")), 1)
            self.assertEqual(len(engine_a.build_context("s", "杭州")["items"]), 1)
            engine_b.delete_source("s", 1)
            self.assertEqual(engine_a.retrieve("s", "杭州"), [])
            self.assertEqual(engine_a.build_context("s", "杭州")["items"], [])
            a.close()
            b.close()

    def test_repeated_revise_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "杭州"))
            new_source = SourceEnvelope("s", 2, "sha", "t", "上海")
            first = engine.revise_source("s", 1, new_source)
            second = engine.revise_source("s", 1, new_source)
            self.assertEqual(first, second)
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM event_candidate").fetchone()[0], 2)
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM revision_receipt").fetchone()[0], 1)
            store.close()


    def test_same_connection_store_delete_invalidates_context(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            self.assertEqual(len(engine.build_context("s", "茶")["items"]), 1)
            engine.store.delete_source("s", 1)
            self.assertEqual(engine.build_context("s", "茶")["items"], [])
            store.close()

    def test_revision_identity_hash_conflict_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "杭州"))
            engine.revise_source("s", 1, SourceEnvelope("s", 2, "hash-a", "t", "上海"))
            with self.assertRaises(ValueError):
                engine.revise_source("s", 1, SourceEnvelope("s", 2, "hash-b", "t", "北京"))
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM revision_receipt").fetchone()[0], 1)
            store.close()


    def test_hybrid_encoder_retrieves_semantic_match(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            class Encoder:
                def encode(self, texts):
                    return [[1.0, 0.0] if "杭州" in text else [0.0, 1.0]
                            for text in texts]
            engine = MemoryEngine(store, encoder=Encoder())
            engine.ingest("s", SourceEnvelope("s", 1, "sha1", "t", "杭州"))
            engine.ingest("s", SourceEnvelope("s", 2, "sha2", "t", "上海"))
            hits = engine.retrieve("s", "杭州", limit=2)
            self.assertEqual(hits[0]["title"], "杭州")
            store.close()

    def test_retrieve_min_score_can_abstain(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            self.assertEqual(len(engine.retrieve("s", "茶", min_score=0.1)), 1)
            self.assertEqual(engine.retrieve("s", "茶", min_score=1000), [])
            store.close()


    def test_multi_step_revision_chain(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "杭州"))
            engine.revise_source("s", 1, SourceEnvelope("s", 2, "sha", "t", "上海"))
            final = engine.revise_source("s", 2, SourceEnvelope("s", 3, "sha", "t", "北京"))
            self.assertEqual(store.events_for_source("s", 1), [])
            self.assertEqual(store.events_for_source("s", 2), [])
            self.assertEqual(store.events_for_source("s", 3), [final])
            self.assertEqual(engine.retrieve("s", "北京")[0]["event_id"], final)
            relations = store.db.execute(
                "SELECT COUNT(*) FROM event_relation WHERE relation_type='SUPERSEDES'").fetchone()[0]
            self.assertEqual(relations, 2)
            store.close()


    def test_history_tracks_supersedes_chain(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            engine = MemoryEngine(store)
            engine.ingest("s", SourceEnvelope("s", 1, "sha", "t", "杭州"))
            engine.revise_source("s", 1, SourceEnvelope("s", 2, "sha", "t", "上海"))
            final = engine.revise_source("s", 2, SourceEnvelope("s", 3, "sha", "t", "北京"))
            history = engine.history("s", final)
            self.assertEqual(len(history["events"]), 3)
            self.assertEqual(len(history["relations"]), 2)
            store.close()


if __name__ == "__main__":
    unittest.main()
