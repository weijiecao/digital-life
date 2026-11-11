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
            self.assertEqual(result, {"processed": 1, "resolved": 1, "deferred": 0, "stale": 0})
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


    def test_provider_omitting_provenance_uses_stored_pending(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            event_id = self._seed(store)
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            store.save_pending(
                "s", 1, [{"candidate_event_id": event_id, "label": "SAME_EVENT"}],
                {event_id: [event_id]}, "split", source=source,
                provenance=NarrativeProvenance("user", "story_character",
                                               "fictional", "story-1"))
            store.enqueue_ready("s", 1, "recheck")
            def judge(scope, src):
                return JudgmentResult(source_seq=1, proposals=[
                    RelationProposal(event_id, "SAME_EVENT")])
            result = PendingRejudger(store, judge).run_once("s")
            self.assertEqual(result["deferred"], 1)
            self.assertEqual(store.events_for_source("s", 1), [])
            self.assertEqual(store.pending_for_source("s", 1)["status"], "PENDING")
            store.close()

    def test_stale_generation_does_not_commit(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            target = self._seed(store)
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            store.save_pending("s", 1, [{"candidate_event_id": target,
                                         "label": "SAME_EVENT"}],
                               {target: [target]}, "split", source=source,
                               provenance=NarrativeProvenance("user", "user", "real"))
            store.enqueue_ready("s", 1, "recheck", generation=1)
            def judge(scope, src):
                store.enqueue_ready(scope, 1, "new", generation=2)
                return JudgmentResult(source_seq=src.source_seq, proposals=[
                    RelationProposal(target, "SAME_EVENT")])
            result = PendingRejudger(store, judge).run_once("s")
            self.assertEqual(result["stale"], 1)
            self.assertEqual(store.events_for_source("s", 1), [])
            self.assertEqual(store.pending_for_source("s", 1)["status"], "PENDING")
            self.assertEqual(store.ready_generation("s", 1), 2)
            store.close()


    def test_partial_batch_failure_keeps_remaining_ready(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            target = self._seed(store)
            for seq in (1, 2):
                source = SourceEnvelope("s", seq, "sha", "t", "item-%d" % seq)
                store.save_pending("s", seq, [{"candidate_event_id": target,
                                               "label": "SAME_EVENT"}],
                                   {target: [target]}, "split", source=source,
                                   provenance=NarrativeProvenance("user", "user", "real"))
                store.enqueue_ready("s", seq, "recheck")
            calls = {"n": 0}
            def judge(scope, src):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("provider timeout")
                return JudgmentResult(source_seq=src.source_seq, proposals=[
                    RelationProposal(target, "SAME_EVENT")])
            with self.assertRaises(RuntimeError):
                PendingRejudger(store, judge).run_once("s")
            self.assertEqual(store.events_for_source("s", 1), [target])
            self.assertEqual(store.events_for_source("s", 2), [])
            self.assertEqual(store.ready_generation("s", 2), 1)
            result = PendingRejudger(store, judge).run_once("s")
            self.assertEqual(result["resolved"], 1)
            self.assertEqual(store.events_for_source("s", 2), [target])
            store.close()


    def test_retry_attempts_resolves_transient_failure(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            target = self._seed(store)
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            store.save_pending("s", 1, [{"candidate_event_id": target,
                                         "label": "SAME_EVENT"}],
                               {target: [target]}, "split", source=source,
                               provenance=NarrativeProvenance("user", "user", "real"))
            store.enqueue_ready("s", 1, "recheck")
            calls = {"n": 0}
            def judge(scope, src):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient")
                return JudgmentResult(source_seq=src.source_seq, proposals=[
                    RelationProposal(target, "SAME_EVENT")])
            result = PendingRejudger(store, judge).run_once("s", attempts=2)
            self.assertEqual(result["resolved"], 1)
            self.assertEqual(store.events_for_source("s", 1), [target])
            store.close()


if __name__ == "__main__":
    unittest.main()
