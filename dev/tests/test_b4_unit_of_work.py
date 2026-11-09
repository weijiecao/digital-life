import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.contracts import SourceEnvelope
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


if __name__ == "__main__":
    unittest.main()
