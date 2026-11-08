import tempfile
import unittest
from pathlib import Path

from evaluation.b4_frozen_replay import _add_map, _load_map
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
            store.close()
            reopened = V15Store(path)
            try:
                self.assertEqual(_load_map(reopened, "s")["old"], {"new-1", "new-2"})
            finally:
                reopened.close()

    def test_gate_audit_is_single_row_for_block(self):
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
