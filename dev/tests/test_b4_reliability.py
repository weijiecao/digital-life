import hashlib
import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.engine import Consolidator
from v1_5_consolidator.gateway import ContextGateway, MockProvider
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


def make_v1(path: Path, text="酒店订在市南区"):
    db = sqlite3.connect(str(path))
    db.execute("""CREATE TABLE dl_capture(
        seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
        event_hash TEXT NOT NULL, content TEXT NOT NULL, content_sha TEXT NOT NULL,
        recorded_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending')""")
    db.execute("INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at,state)"
               " VALUES(1,'scope-a','h',?,?,?, 'pending')",
               (text, hashlib.sha256(text.encode()).hexdigest(), "2026-11-07T10:00:00+00:00"))
    db.commit()
    db.close()


def seed_event(store, event_id, scope="scope-a"):
    now = "2026-11-07T10:00:00+00:00"
    store.db.execute("""INSERT OR IGNORE INTO event_candidate
        (event_id,scope_id,event_type,title,state,current_revision,created_at,updated_at)
        VALUES(?,?,?,?,'provisional',1,?,?)""", (event_id, scope, "plan", event_id, now, now))
    store.db.execute("""INSERT OR IGNORE INTO event_revision
        (event_id,rev,summary,status,modality,created_at) VALUES(?,1,?,?,?,?)""",
        (event_id, event_id, "unknown", "unknown", now))
    store.db.commit()


class FailingProvider:
    external = False

    def generate(self, prepared):
        raise RuntimeError("provider 503")


def _r07_child(path: str) -> None:
    store = V15Store(path)
    store.db.execute("BEGIN")
    store.db.execute("""INSERT INTO event_candidate
        (event_id,scope_id,event_type,title,state,current_revision,created_at,updated_at)
        VALUES('evt_r07','scope-a','plan','r07','provisional',1,'t','t')""")
    store.db.execute("""INSERT INTO event_revision
        (event_id,rev,summary,status,modality,created_at)
        VALUES('evt_r07',1,'r07','unknown','unknown','t')""")
    os._exit(7)


def _r08_child(path: str) -> None:
    store = V15Store(path)
    seed_event(store, "evt_r08")
    source = SourceEnvelope("scope-a", 1, "sha", "t", "酒店")
    ConstrainedResolver(store).resolve("scope-a", source, JudgmentResult(
        source_seq=1, proposals=[RelationProposal("evt_r08", "SAME_EVENT")]))
    os._exit(8)


class TestB4Reliability(unittest.TestCase):
    def test_restart_idempotence(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            store = V15Store(path)
            seed_event(store, "evt_a")
            source = SourceEnvelope("scope-a", 1, "sha", "t", "酒店")
            judgment = JudgmentResult(source_seq=1, proposals=[RelationProposal("evt_a", "SAME_EVENT")])
            ConstrainedResolver(store).resolve("scope-a", source, judgment)
            store.close()
            reopened = V15Store(path)
            try:
                result = ConstrainedResolver(reopened).resolve("scope-a", source, judgment)
                self.assertEqual(result.already_attached, 1)
                self.assertEqual(reopened.events_for_source("scope-a", 1), ["evt_a"])
            finally:
                reopened.close()

    def test_multiple_same_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed_event(store, "evt_a")
            seed_event(store, "evt_b")
            result = ConstrainedResolver(store).resolve("scope-a", SourceEnvelope("scope-a", 1, "sha", "t", "x"),
                                                        JudgmentResult(source_seq=1, proposals=[
                                                            RelationProposal("evt_a", "SAME_EVENT"),
                                                            RelationProposal("evt_b", "SAME_EVENT")]))
            self.assertEqual(result.conflicts, 2)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)
            store.close()

    def test_cannot_link_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            store = V15Store(path)
            seed_event(store, "evt_a")
            seed_event(store, "evt_b")
            store.add_cannot_link("scope-a", "evt_a", "evt_b", 1)
            source = SourceEnvelope("scope-a", 1, "sha", "t", "x")
            store.attach_source("scope-a", source, "evt_b", "seed")
            store.close()
            reopened = V15Store(path)
            try:
                result = ConstrainedResolver(reopened).resolve("scope-a", source, JudgmentResult(
                    source_seq=1, proposals=[RelationProposal("evt_a", "SAME_EVENT")]))
                self.assertEqual(result.conflicts, 1)
                self.assertEqual(result.attached, 0)
            finally:
                reopened.close()

    def test_related_no_attach(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed_event(store, "evt_a")
            result = ConstrainedResolver(store).resolve("scope-a", SourceEnvelope("scope-a", 1, "sha", "t", "x"),
                                                        JudgmentResult(source_seq=1, proposals=[
                                                            RelationProposal("evt_a", "RELATED_CONTEXT")]))
            self.assertEqual(result.related, 1)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)
            store.close()

    def test_cross_scope_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed_event(store, "evt_other", scope="scope-b")
            result = ConstrainedResolver(store).resolve("scope-a", SourceEnvelope("scope-a", 1, "sha", "t", "x"),
                                                        JudgmentResult(source_seq=1, proposals=[
                                                            RelationProposal("evt_other", "SAME_EVENT")]))
            self.assertEqual(result.conflicts, 1)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)
            store.close()

    def test_deleted_source_invalidates_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed_event(store, "evt_a")
            source = SourceEnvelope("scope-a", 1, "sha", "t", "x")
            ConstrainedResolver(store).resolve("scope-a", source, JudgmentResult(
                source_seq=1, proposals=[RelationProposal("evt_a", "SAME_EVENT")]))
            store.sync_inventory([("scope-a", 1, "sha")], "scan-1")
            store.sync_inventory([], "delete-scan")
            self.assertEqual(store.db.execute("SELECT valid FROM event_evidence").fetchone()[0], 0)
            self.assertEqual(store.db.execute("SELECT state FROM event_candidate").fetchone()[0], "ambiguous")
            store.close()

    def test_r07_kill_before_commit_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            process = multiprocessing.Process(target=_r07_child, args=(path,))
            process.start()
            process.join(30)
            self.assertEqual(process.exitcode, 7)
            store = V15Store(path)
            try:
                self.assertEqual(
                    store.db.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0], 0)
                self.assertEqual(
                    store.db.execute("SELECT COUNT(*) FROM event_revision").fetchone()[0], 0)
            finally:
                store.close()

    def test_r08_kill_after_commit_before_cursor_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            process = multiprocessing.Process(target=_r08_child, args=(path,))
            process.start()
            process.join(30)
            self.assertEqual(process.exitcode, 8)
            store = V15Store(path)
            try:
                self.assertEqual(
                    store.db.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0], 1)
                self.assertEqual(store.cursor("scope-a"), 0)
                source = SourceEnvelope("scope-a", 1, "sha", "t", "酒店")
                result = ConstrainedResolver(store).resolve("scope-a", source,
                                                            JudgmentResult(source_seq=1, proposals=[
                                                                RelationProposal("evt_r08", "SAME_EVENT")]))
                self.assertEqual(result.already_attached, 1)
                self.assertEqual(
                    store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 1)
            finally:
                store.close()

    def test_provider_failure_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            v1 = Path(d) / "v1.sqlite3"
            v15 = Path(d) / "v15.sqlite3"
            make_v1(v1)
            c = Consolidator(str(v1), str(v15), FailingProvider(), min_messages=1)
            try:
                with self.assertRaises(RuntimeError):
                    c.run_once(force=True)
                self.assertEqual(c.store.cursor("scope-a"), 0)
            finally:
                c.close()

    def test_context_budget_failure_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            v1 = Path(d) / "v1.sqlite3"
            v15 = Path(d) / "v15.sqlite3"
            make_v1(v1)
            store = V15Store(str(v15))
            gateway = ContextGateway(store, max_chars=10)
            c = Consolidator(str(v1), str(v15), MockProvider(), gateway=gateway,
                             min_messages=1, store=store)
            try:
                with self.assertRaises(ValueError):
                    c.run_once(force=True)
                self.assertEqual(c.store.cursor("scope-a"), 0)
            finally:
                c.close()


if __name__ == "__main__":
    unittest.main()
