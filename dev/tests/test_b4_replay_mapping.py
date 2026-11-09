import tempfile
import unittest
from pathlib import Path

from evaluation.b4_frozen_replay import _add_map, _load_map, map_proposals, mapping_status
from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


def _with_replay_table(store):
    store.db.execute("""CREATE TABLE IF NOT EXISTS replay_map(
        scope_id TEXT NOT NULL, old_event_id TEXT NOT NULL, event_id TEXT NOT NULL,
        PRIMARY KEY(scope_id,old_event_id,event_id))""")
    store.db.commit()


class TestReplayMapping(unittest.TestCase):
    def test_duplicate_mapping_is_not_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            _with_replay_table(store)
            _add_map(store, "s", "old", "new-1")
            _add_map(store, "s", "old", "new-1")
            self.assertEqual(_load_map(store, "s")["old"], {"new-1"})
            store.close()

    def test_two_targets_are_ambiguous_and_persist(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            store = V15Store(path)
            _with_replay_table(store)
            _add_map(store, "s", "old", "new-1")
            _add_map(store, "s", "old", "new-2")
            store.db.commit()
            self.assertEqual(_load_map(store, "s")["old"], {"new-1", "new-2"})
            self.assertEqual(mapping_status(_load_map(store, "s"), "old"),
                             "AMBIGUOUS_SPLIT")
            store.close()
            reopened = V15Store(path)
            try:
                self.assertEqual(_load_map(reopened, "s")["old"], {"new-1", "new-2"})
            finally:
                reopened.close()

    def test_unique_and_missing_mapping_status(self):
        mapping = {"old": {"new-1"}}
        self.assertEqual(mapping_status(mapping, "old"), "UNIQUE")
        self.assertEqual(mapping_status(mapping, "unknown"), "MISSING")

    def test_gate_audit_records_allow_for_user_real(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            now = "2026-11-08T10:00:00+00:00"
            store.db.execute("""INSERT INTO event_candidate
                (event_id,scope_id,event_type,title,state,current_revision,
                 narrator,subject,reality_status,created_at,updated_at)
                VALUES('evt-real','s','plan','real','provisional',1,
                'user','user','real',?,?)""", (now, now))
            store.db.execute("""INSERT INTO event_revision
                (event_id,rev,summary,status,modality,created_at)
                VALUES('evt-real',1,'real','unknown','unknown',?)""", (now,))
            store.db.commit()
            ConstrainedResolver(store).resolve(
                "s", SourceEnvelope("s", 1, "sha", "t", "我去了"),
                JudgmentResult(source_seq=1, proposals=[
                    RelationProposal("evt-real", "SAME_EVENT")],
                    provenance=NarrativeProvenance("user", "user", "real")))
            row = store.db.execute(
                "SELECT action,resolver_decision FROM gate_audit").fetchone()
            self.assertEqual(tuple(row), ("ALLOW", "ATTACHED"))
            store.close()

    def test_split_plus_unique_is_not_silently_reduced(self):
        entry = {"proposals": [
            {"candidate_event_id": "old-A", "label": "SAME_EVENT"},
            {"candidate_event_id": "old-B", "label": "SAME_EVENT"}]}
        mapping = {"old-A": {"new-A1", "new-A2"}, "old-B": {"new-B"}}
        result = map_proposals(entry, mapping)
        self.assertFalse(result.safe_to_resolve)
        self.assertEqual(len(result.proposals), 2)
        self.assertEqual([p.mapping_status for p in result.proposals],
                         ["AMBIGUOUS_SPLIT", "UNIQUE"])

    def test_gate_audit_records_allow_then_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            now = "2026-11-08T10:00:00+00:00"
            for eid in ("evt-a", "evt-b"):
                store.db.execute("""INSERT INTO event_candidate
                    (event_id,scope_id,event_type,title,state,current_revision,
                     narrator,subject,reality_status,created_at,updated_at)
                    VALUES(?, 's','plan',?,'provisional',1,'user','user','real',?,?)""",
                    (eid, eid, now, now))
                store.db.execute("""INSERT INTO event_revision
                    (event_id,rev,summary,status,modality,created_at)
                    VALUES(?,1,?,'unknown','unknown',?)""", (eid, eid, now))
            store.db.commit()
            source2 = SourceEnvelope("s", 2, "sha", "t", "b")
            store.attach_source("s", source2, "evt-a", "p")
            store.add_cannot_link("s", "evt-a", "evt-b", 2)
            ConstrainedResolver(store).resolve(
                "s", source2,
                JudgmentResult(source_seq=2, proposals=[
                    RelationProposal("evt-b", "SAME_EVENT")],
                    provenance=NarrativeProvenance("user", "user", "real")))
            row = store.db.execute(
                "SELECT action,resolver_decision FROM gate_audit").fetchone()
            self.assertEqual(tuple(row), ("ALLOW", "CONFLICT"))
            self.assertEqual(store.events_for_source("s", 2), ["evt-a"])
            store.close()
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            now = "2026-11-08T10:00:00+00:00"
            store.db.execute("""INSERT INTO event_candidate
                (event_id,scope_id,event_type,title,state,current_revision,
                 narrator,subject,reality_status,created_at,updated_at)
                VALUES('evt-real','s','plan','real','provisional',1,
                'user','user','real',?,?)""", (now, now))
            store.db.execute("""INSERT INTO event_revision
                (event_id,rev,summary,status,modality,created_at)
                VALUES('evt-real',1,'real','unknown','unknown',?)""", (now,))
            store.db.commit()
            resolver = ConstrainedResolver(store)
            resolver.resolve("s", SourceEnvelope("s", 1, "sha", "t", "故事"),
                             JudgmentResult(source_seq=1, proposals=[
                                 RelationProposal("evt-real", "SAME_EVENT")],
                                 provenance=NarrativeProvenance(
                                     "story_character", "story_character", "fictional", "s1")))
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM gate_audit").fetchone()[0], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
