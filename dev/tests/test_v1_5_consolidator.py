import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.engine import Consolidator, ProposalValidator, _digest, _merge_followups
from v1_5_consolidator.contracts import ProposalOperation, SourceEnvelope
from v1_5_consolidator.gateway import MockProvider
from v1_5_consolidator.store import V15Store


def make_v1(path: Path, rows):
    db = sqlite3.connect(str(path))
    db.execute("""CREATE TABLE dl_capture(
        seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
        event_hash TEXT NOT NULL, content TEXT NOT NULL, content_sha TEXT NOT NULL,
        recorded_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending')""")
    for seq, scope, text in rows:
        db.execute("INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at,state)"
                   " VALUES(?,?,?,?,?,?,?)",
                   (seq, scope, "h%d" % seq, text,
                    hashlib.sha256(text.encode()).hexdigest(),
                    "2026-11-07T10:00:00+00:00", "pending"))
    db.commit()
    db.close()


class TestV15Consolidator(unittest.TestCase):
    def test_mock_run_creates_candidate_and_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            v1 = Path(d) / "v1.sqlite3"
            v15 = Path(d) / "v15.sqlite3"
            make_v1(v1, [(1, "scope-a", "我计划下个月去四姑娘山")])
            c = Consolidator(str(v1), str(v15), MockProvider(), min_messages=1, batch_limit=10)
            try:
                summary = c.run_once(force=True)
                self.assertEqual(summary.events_created, 1)
                status = c.store.status()
                self.assertEqual(status["events"], 1)
                self.assertEqual(status["evidence"], 1)
                self.assertEqual(c.store.cursor("scope-a"), 1)
                # 第二次没有新来源，不重复创建。
                c.run_once(force=True)
                self.assertEqual(c.store.status()["events"], 1)
            finally:
                c.close()

    def test_bad_quote_is_rejected_and_cursor_not_advanced(self):
        with tempfile.TemporaryDirectory() as d:
            v1 = Path(d) / "v1.sqlite3"
            v15 = Path(d) / "v15.sqlite3"
            make_v1(v1, [(1, "scope-a", "我计划去四姑娘山")])
            response = {"operations": [{
                "action": "create", "event_type": "plan", "title": "x",
                "summary": "x", "modality": "planned", "status_proposal": "planned",
                "source_refs": [{"source_seq": 1, "quote": "不存在的原文"}],
            }]}
            c = Consolidator(str(v1), str(v15), MockProvider(response),
                             min_messages=1, batch_limit=10)
            try:
                summary = c.run_once(force=True)
                self.assertEqual(summary.events_created, 0)
                self.assertGreater(summary.rejected, 0)
                self.assertEqual(c.store.cursor("scope-a"), 0)
            finally:
                c.close()

    def test_validator_rejects_cross_scope_candidate(self):
        sources = [SourceEnvelope("a", 1, "sha", "t", "hello")]
        validator = ProposalValidator("a", sources, [{"event_id": "evt_x"}])
        result = validator.validate({"operations": [{
            "action": "update", "event_type": "plan", "candidate_event_id": "evt_y",
            "source_refs": [{"source_seq": 1, "quote": "hello"}],
            "summary": "x", "modality": "planned", "status_proposal": "planned"}]})
        self.assertEqual(len(result.accepted), 0)
        self.assertEqual(len(result.rejected), 1)

    def test_followup_source_is_merged_into_existing_event(self):
        sources = [
            SourceEnvelope("a", 1, "sha1", "t", "下个月十五号搬到杭州西湖区"),
            SourceEnvelope("a", 2, "sha2", "t", "之前搬家到杭州西湖区那件事怎么样"),
        ]
        first = ProposalOperation(action="create", event_type="plan",
                                  title="搬家计划", summary="搬家到杭州西湖区",
                                  source_refs=[{"source_seq": 1, "quote": sources[0].text}])
        follow = ProposalOperation(action="create", event_type="unknown",
                                   title="之前搬家", summary="回顾",
                                   source_refs=[{"source_seq": 2, "quote": sources[1].text}])
        merged = _merge_followups([first, follow], sources)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].source_refs), 2)

    def test_tombstone_invalidates_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            v1 = Path(d) / "v1.sqlite3"
            v15 = Path(d) / "v15.sqlite3"
            make_v1(v1, [(1, "scope-a", "我喜欢吃苹果")])
            c = Consolidator(str(v1), str(v15), MockProvider(), min_messages=1, batch_limit=10)
            try:
                c.run_once(force=True)
                db = sqlite3.connect(str(v1))
                db.execute("DELETE FROM dl_capture WHERE seq=1")
                db.commit()
                db.close()
                missing = c.store.sync_inventory([], "scan-delete")
                self.assertEqual(missing, 1)
                ev = c.store.db.execute("SELECT state FROM event_candidate").fetchone()[0]
                self.assertEqual(ev, "ambiguous")
                valid = c.store.db.execute("SELECT valid FROM event_evidence").fetchone()[0]
                self.assertEqual(valid, 0)
            finally:
                c.close()


if __name__ == "__main__":
    unittest.main()
