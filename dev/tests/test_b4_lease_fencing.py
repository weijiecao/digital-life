import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


def _seed_event(store, event_id="evt-a", scope="scope-a"):
    now = "2026-11-08T10:00:00+00:00"
    store.db.execute("""INSERT OR IGNORE INTO event_candidate
        (event_id,scope_id,event_type,title,state,current_revision,created_at,updated_at)
        VALUES(?,?,?,?,'provisional',1,?,?)""",
        (event_id, scope, "plan", event_id, now, now))
    store.db.execute("""INSERT OR IGNORE INTO event_revision
        (event_id,rev,summary,status,modality,created_at) VALUES(?,1,?,'unknown','unknown',?)""",
        (event_id, event_id, now))
    store.db.commit()


class TestLeaseFencing(unittest.TestCase):
    def test_active_lease_blocks_second_owner(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            first = store.claim_job("scope-a", 1, 10, "d1", "worker-A", 30)
            self.assertIsNotNone(first)
            self.assertIsNone(store.claim_job("scope-a", 1, 10, "d1", "worker-B", 30))
            self.assertTrue(store.renew_lease(first[0], "worker-A", first[1], 30))
            self.assertTrue(store.commit_job(first[0], "worker-A", first[1], 10))
            self.assertEqual(store.cursor("scope-a"), 10)
            self.assertIsNone(store.claim_job("scope-a", 1, 10, "d1", "worker-B", 30))
            store.close()

    def test_expired_lease_takeover_fences_old_owner(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            first = store.claim_job("scope-a", 1, 10, "d2", "worker-A", 0)
            second = store.claim_job("scope-a", 1, 10, "d2", "worker-B", 30)
            self.assertEqual(second[1], first[1] + 1)
            self.assertFalse(store.commit_job(first[0], "worker-A", first[1], 10))
            self.assertTrue(store.commit_job(second[0], "worker-B", second[1], 10))
            store.close()

    def test_stale_token_cannot_renew_or_fail(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            first = store.claim_job("scope-a", 1, 10, "d3", "worker-A", 0)
            second = store.claim_job("scope-a", 1, 10, "d3", "worker-B", 30)
            self.assertFalse(store.renew_lease(first[0], "worker-A", first[1], 30))
            self.assertFalse(store.fail_job(first[0], "worker-A", first[1]))
            store.close()

    def test_retryable_failure_can_be_reclaimed(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            first = store.claim_job("scope-a", 1, 10, "d4", "worker-A", 30)
            self.assertTrue(store.fail_job(first[0], "worker-A", first[1], retryable=True))
            second = store.claim_job("scope-a", 1, 10, "d4", "worker-B", 30)
            self.assertIsNotNone(second)
            self.assertTrue(store.commit_job(second[0], "worker-B", second[1], 10))
            store.close()

    def test_r15_batch_failure_replays_idempotently(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            store = V15Store(path)
            _seed_event(store)
            job = store.claim_job("scope-a", 1, 2, "r15", "worker-A", 30)
            resolver = ConstrainedResolver(store)
            first = SourceEnvelope("scope-a", 1, "s1", "t", "酒店订在市南区")
            resolver.resolve("scope-a", first, JudgmentResult(
                source_seq=1, proposals=[RelationProposal("evt-a", "SAME_EVENT")]))
            # Fail before the batch commit; reopen and reclaim.
            store.fail_job(job[0], "worker-A", job[1], retryable=True)
            store.close()
            store = V15Store(path)
            try:
                replay = store.claim_job("scope-a", 1, 2, "r15", "worker-B", 30)
                self.assertIsNotNone(replay)
                resolver = ConstrainedResolver(store)
                resolver.resolve("scope-a", first, JudgmentResult(
                    source_seq=1, proposals=[RelationProposal("evt-a", "SAME_EVENT")]))
                second = SourceEnvelope("scope-a", 2, "s2", "t", "预算三千")
                resolver.resolve("scope-a", second, JudgmentResult(
                    source_seq=2, proposals=[RelationProposal("evt-a", "SAME_EVENT")]))
                self.assertTrue(store.commit_job(replay[0], "worker-B", replay[1], 2))
                self.assertEqual(store.db.execute(
                    "SELECT COUNT(*) FROM event_evidence").fetchone()[0], 2)
                self.assertEqual(store.events_for_source("scope-a", 1), ["evt-a"])
            finally:
                store.close()

    def test_r16_committed_batch_is_not_reprocessed(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            _seed_event(store)
            job = store.claim_job("scope-a", 1, 1, "r16", "worker-A", 30)
            ConstrainedResolver(store).resolve("scope-a", SourceEnvelope(
                "scope-a", 1, "s1", "t", "酒店"), JudgmentResult(
                source_seq=1, proposals=[RelationProposal("evt-a", "SAME_EVENT")]))
            self.assertTrue(store.commit_job(job[0], "worker-A", job[1], 1))
            self.assertIsNone(store.claim_job("scope-a", 1, 1, "r16", "worker-B", 30))
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM event_evidence").fetchone()[0], 1)
            store.close()

    def test_scope_jobs_are_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            a = store.claim_job("scope-a", 1, 10, "da", "worker-A", 30)
            b = store.claim_job("scope-b", 1, 10, "db", "worker-B", 30)
            self.assertTrue(store.commit_job(a[0], "worker-A", a[1], 10))
            self.assertTrue(store.commit_job(b[0], "worker-B", b[1], 10))
            self.assertEqual(store.cursor("scope-a"), 10)
            self.assertEqual(store.cursor("scope-b"), 10)
            store.close()


if __name__ == "__main__":
    unittest.main()
