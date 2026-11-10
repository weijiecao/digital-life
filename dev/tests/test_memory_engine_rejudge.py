import tempfile
import unittest
from pathlib import Path

from memory_engine.rejudge import PendingRejudger
from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.store import V15Store


class TestPendingRejudger(unittest.TestCase):
    def _seed(self, store):
        return store.create_event_from_source(
            "s", SourceEnvelope("s", 99, "sha", "t", "seed"),
            provenance={"narrator": "user", "subject": "user",
                        "reality_status": "real", "story_id": None})

    def test_rejudge_resolves_pending(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            event_id = self._seed(store)
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            store.save_pending("s", 1, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": [event_id]}, "split", source=source)
            store.enqueue_ready("s", 1, "recheck")
            def judge(scope, src):
                return JudgmentResult(source_seq=1, proposals=[
                    RelationProposal(event_id, "SAME_EVENT")],
                    provenance=NarrativeProvenance("user", "user", "real"))
            result = PendingRejudger(store, judge).run_once("s")
            self.assertEqual(result, {"processed": 1, "resolved": 1, "deferred": 0})
            self.assertEqual(store.events_for_source("s", 1), [event_id])
            self.assertEqual(store.pending_for_source("s", 1)["status"], "RELOCATED")
            self.assertEqual(store.poll_ready("s"), [])
            store.close()

    def test_rejudge_deferred_keeps_pending(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            event_id = self._seed(store)
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            store.save_pending("s", 1, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": [event_id]}, "split", source=source)
            store.enqueue_ready("s", 1, "recheck")
            def judge(scope, src):
                return JudgmentResult(source_seq=1, proposals=[
                    RelationProposal(event_id, "DIFFERENT_EVENT")])
            result = PendingRejudger(store, judge).run_once("s")
            self.assertEqual(result["deferred"], 1)
            self.assertEqual(store.events_for_source("s", 1), [])
            self.assertEqual(store.pending_for_source("s", 1)["status"], "PENDING")
            store.close()


    def test_uncertain_does_not_reuse_old_provisional(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            event_id = self._seed(store)
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            store.attach_source("s", source, event_id, "old")
            store.save_pending("s", 1, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": [event_id]}, "split", source=source)
            store.enqueue_ready("s", 1, "recheck")
            def judge(scope, src):
                return JudgmentResult(source_seq=1, proposals=[
                    RelationProposal(event_id, "UNCERTAIN")])
            result = PendingRejudger(store, judge).run_once("s")
            self.assertEqual(result["deferred"], 1)
            self.assertEqual(store.pending_for_source("s", 1)["status"], "PENDING")
            store.close()


if __name__ == "__main__":
    unittest.main()
