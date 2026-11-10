import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestUnitOfWork(unittest.TestCase):
    def test_unit_commit(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            with store.unit_of_work():
                event_id = store.create_event_from_source("s", source)
                store.attach_source("s", source, event_id, "p")
                store.record_gate_audit("r", "s", 1, "ProvenanceGuard", "ALLOW",
                                        ["ok"], "p1", event_id, "SAME_EVENT", "ATTACHED")
            self.assertEqual(store.events_for_source("s", 1), [event_id])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 1)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM gate_audit").fetchone()[0], 1)
            store.close()

    def test_unit_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            with self.assertRaises(RuntimeError):
                with store.unit_of_work():
                    event_id = store.create_event_from_source("s", source)
                    store.attach_source("s", source, event_id, "p")
                    raise RuntimeError("crash")
            self.assertEqual(store.events_for_source("s", 1), [])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0], 0)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)
            store.close()

    def test_nested_units_do_not_commit_early(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            with self.assertRaises(RuntimeError):
                with store.unit_of_work():
                    with store.unit_of_work():
                        event_id = store.create_event_from_source("s", source)
                    raise RuntimeError("outer crash")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0], 0)
            store.close()


    def test_inner_exception_caught_still_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            with self.assertRaises(RuntimeError):
                with store.unit_of_work():
                    try:
                        with store.unit_of_work():
                            store.create_event_from_source("s", source)
                            raise RuntimeError("inner")
                    except RuntimeError:
                        pass
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0], 0)
            store.close()

    def test_pending_rolls_back_and_relocates(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            with self.assertRaises(RuntimeError):
                with store.unit_of_work():
                    store.save_pending("s", 1, [{"candidate_event_id": "old",
                                                 "label": "SAME_EVENT"}],
                                       {"old": ["new-a", "new-b"]}, "split")
                    raise RuntimeError("crash")
            self.assertIsNone(store.pending_for_source("s", 1))
            with store.unit_of_work():
                store.save_pending("s", 2, [{"candidate_event_id": "old",
                                             "label": "SAME_EVENT"}],
                                   {"old": ["new-a", "new-b"]}, "split")
            pending = store.pending_for_source("s", 2)
            self.assertEqual(pending["status"], "PENDING")
            self.assertEqual(len(pending["proposals"]), 1)
            self.assertTrue(store.mark_pending_relocated("s", 2, "new-a"))
            self.assertEqual(store.pending_for_source("s", 2)["status"], "RELOCATED")
            store.close()


    def test_pending_relocation_writes_evidence_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            store.create_event_from_source(
                "s", SourceEnvelope("s", 99, "sha", "t", "seed"),
                event_id="evt", provenance={"narrator": "user", "subject": "user",
                                           "reality_status": "real", "story_id": None})
            store.save_pending("s", 2, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": ["evt"]}, "split",
                               provenance=NarrativeProvenance("user", "user", "real"))
            source = SourceEnvelope("s", 2, "sha", "t", "resolved")
            self.assertTrue(store.relocate_pending(
                "s", 2, source, "evt",
                source_provenance=NarrativeProvenance("user", "user", "real")))
            self.assertEqual(store.events_for_source("s", 2), ["evt"])
            self.assertEqual(store.pending_for_source("s", 2)["status"], "RELOCATED")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM gate_audit").fetchone()[0], 1)
            store.close()


    def test_effective_read_filters_pending_and_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            source = SourceEnvelope("s", 1, "sha", "t", "hello")
            event_id = store.create_event_from_source("s", source)
            store.attach_source("s", source, event_id, "p")
            self.assertEqual(store.effective_events_for_source("s", 1), [event_id])
            store.save_pending("s", 1, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": [event_id]}, "split")
            self.assertEqual(store.effective_events_for_source("s", 1), [])
            store.mark_pending_relocated("s", 1, event_id)
            self.assertEqual(store.effective_events_for_source("s", 1), [event_id])
            store.invalidate_source_evidence("s", 1)
            self.assertEqual(store.effective_events_for_source("s", 1), [])
            store.close()


    def test_auto_relocate_only_when_mapping_is_unique(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed = SourceEnvelope("s", 99, "sha", "t", "seed")
            event_id = store.create_event_from_source(
                "s", seed, provenance={"narrator": "user", "subject": "user",
                                       "reality_status": "real", "story_id": None})
            source = SourceEnvelope("s", 2, "sha", "t", "pending text")
            proposals = [{"candidate_event_id": "old", "label": "SAME_EVENT"}]
            store.save_pending("s", 2, proposals, {"old": ["new-a", "new-b"]},
                               "split", source=source)
            self.assertEqual(store.auto_relocate_pending("s")["withheld"], 1)
            self.assertEqual(store.effective_events_for_source("s", 2), [])
            store.save_pending("s", 2, proposals, {"old": [event_id]}, "resolved",
                               source=source, provenance=NarrativeProvenance("user", "user", "real"))
            self.assertEqual(store.auto_relocate_pending("s")["relocated"], 1)
            self.assertEqual(store.events_for_source("s", 2), [event_id])
            store.close()


    def test_relocation_invalidates_old_membership(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed = SourceEnvelope("s", 99, "sha", "t", "seed")
            old_event = store.create_event_from_source(
                "s", seed, provenance={"narrator": "user", "subject": "user",
                                       "reality_status": "real", "story_id": None})
            new_event = store.create_event_from_source(
                "s", SourceEnvelope("s", 98, "sha", "t", "new"),
                provenance={"narrator": "user", "subject": "user",
                            "reality_status": "real", "story_id": None})
            source = SourceEnvelope("s", 2, "sha", "t", "pending")
            store.attach_source("s", source, old_event, "old")
            store.save_pending("s", 2, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": [new_event]}, "resolved", source=source,
                               provenance=NarrativeProvenance("user", "user", "real"))
            self.assertTrue(store.relocate_pending("s", 2, None, new_event))
            self.assertEqual(store.effective_events_for_source("s", 2), [new_event])
            self.assertEqual(store.events_for_source("s", 2), [new_event])
            store.close()


    def test_auto_relocate_cannot_bypass_provenance_guard(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            event_id = store.create_event_from_source(
                "s", SourceEnvelope("s", 99, "sha", "t", "用户真实事件"),
                provenance={"narrator": "user", "subject": "user",
                            "reality_status": "real", "story_id": None})
            source = SourceEnvelope("s", 2, "sha", "t", "虚构故事里的经历")
            store.save_pending("s", 2, [{"candidate_event_id": event_id,
                                         "label": "SAME_EVENT"}],
                               {event_id: [event_id]}, "relocate", source=source,
                               provenance=NarrativeProvenance(
                                   "user", "story_character", "fictional", "story-1"))
            self.assertEqual(store.auto_relocate_pending("s")["relocated"], 0)
            self.assertEqual(store.events_for_source("s", 2), [])
            gate = store.db.execute(
                "SELECT action FROM gate_audit ORDER BY rowid DESC LIMIT 1").fetchone()
            self.assertEqual(gate[0], "REJECT")
            store.close()


    def test_auto_relocate_respects_cannot_link(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            provisional = store.create_event_from_source(
                "s", SourceEnvelope("s", 1, "sha", "t", "旧 provisional"),
                provenance={"narrator": "user", "subject": "user",
                            "reality_status": "real", "story_id": None})
            target = store.create_event_from_source(
                "s", SourceEnvelope("s", 99, "sha", "t", "目标事件"),
                provenance={"narrator": "user", "subject": "user",
                            "reality_status": "real", "story_id": None})
            source = SourceEnvelope("s", 2, "sha", "t", "待重定位")
            store.attach_source("s", source, provisional, "provisional")
            store.db.execute("""INSERT INTO event_relation
                (scope_id,from_event,to_event,relation_type,status,source_seq,created_at)
                VALUES('s',?,?,'CANNOT_LINK','active',2,'2026-11-10T00:00:00+00:00')""",
                (provisional, target))
            store.save_pending("s", 2, [{"candidate_event_id": target,
                                         "label": "SAME_EVENT"}],
                               {target: [target]}, "relocate", source=source,
                               provenance=NarrativeProvenance("user", "user", "real"))
            self.assertEqual(store.auto_relocate_pending("s")["relocated"], 0)
            self.assertEqual(store.events_for_source("s", 2), [provisional])
            self.assertNotEqual(store.db.execute(
                "SELECT action FROM gate_audit ORDER BY rowid DESC LIMIT 1").fetchone()[0],
                "ALLOW")
            store.close()


if __name__ == "__main__":
    unittest.main()
