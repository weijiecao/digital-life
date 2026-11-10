import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.store import V15Store


class TestScopeLeaseReceipt(unittest.TestCase):
    def test_scope_lease_excludes_second_owner_and_fences_old(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            self.assertEqual(store.claim_scope("s", "worker-a", ttl=30), 1)
            self.assertIsNone(store.claim_scope("s", "worker-b", ttl=30))
            store.db.execute("UPDATE scope_lease SET expires_at=0 WHERE scope_id='s'")
            store.db.commit()
            self.assertEqual(store.claim_scope("s", "worker-b", ttl=30), 2)
            self.assertFalse(store.verify_scope("s", "worker-a", 1))
            self.assertTrue(store.verify_scope("s", "worker-b", 2))
            store.close()

    def test_receipt_idempotent_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            self.assertTrue(store.record_receipt("s", 1, "ATTACHED", "h1"))
            self.assertTrue(store.record_receipt("s", 1, "ATTACHED", "h1"))
            self.assertFalse(store.record_receipt("s", 1, "ATTACHED", "h2"))
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0], 1)
            store.close()

    def test_contiguous_frontier_not_max(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            store.record_receipt("s", 2, "ATTACHED", "h2")
            store.record_receipt("s", 1, "PENDING", "h1")
            self.assertEqual(store.contiguous_frontier("s"), 2)
            store.record_receipt("s", 4, "ATTACHED", "h4")
            self.assertEqual(store.contiguous_frontier("s"), 2)
            store.close()

    def test_dependency_change_enqueues_ready_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            self.assertTrue(store.update_dependency("s", "event", "e1", 1, [1, 2], "created"))
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [1, 2])
            self.assertFalse(store.update_dependency("s", "event", "e1", 1, [3], "same"))
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [1, 2])
            self.assertTrue(store.update_dependency("s", "event", "e1", 2, [3], "changed"))
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [1, 2, 3])
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM dependency_change").fetchone()[0], 2)
            store.close()

    def test_reconcile_enqueues_missing_and_pending(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            store.register_manifest("s", [(1, "a"), (2, "b"), (3, "c")])
            store.record_receipt("s", 1, "ATTACHED", "h1")
            store.save_pending("s", 4, [{"candidate_event_id": "old",
                                         "label": "SAME_EVENT"}],
                               {"old": ["e1", "e2"]}, "split")
            result = store.reconcile_ready("s")
            self.assertEqual(result["missing_receipts"], 2)
            self.assertEqual(result["pending"], 1)
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [2, 3, 4])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)
            store.close()

    def test_ready_queue_dedup_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            store.enqueue_ready("s", 2, "dep-change")
            store.enqueue_ready("s", 1, "dep-change")
            store.enqueue_ready("s", 2, "dep-change")
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [1, 2])
            self.assertTrue(store.mark_ready_done("s", 1))
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [2])
            store.enqueue_ready("s", 1, "new-change")
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [1, 2])
            store.close()

    def test_manifest_sparse_frontier_and_missing_scan(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            store.register_manifest("s", [(10, "a"), (20, "b"), (30, "c")])
            store.record_receipt("s", 10, "ATTACHED", "h10")
            store.record_receipt("s", 30, "ATTACHED", "h30")
            self.assertEqual(store.contiguous_frontier("s"), 10)
            self.assertEqual([m["source_seq"] for m in store.manifest_missing_receipts("s")], [20])
            store.record_receipt("s", 20, "PENDING", "h20")
            self.assertEqual(store.contiguous_frontier("s"), 30)
            self.assertEqual(store.manifest_missing_receipts("s"), [])
            store.close()

    def test_receipt_rolls_back_with_unit(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            with self.assertRaises(RuntimeError):
                with store.unit_of_work():
                    store.record_receipt("s", 1, "ATTACHED", "h1")
                    raise RuntimeError("crash")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0], 0)
            store.close()

    def test_job_commit_requires_current_scope_token(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            scope_token = store.claim_scope("s", "worker-a", ttl=30)
            job = store.claim_job("s", 1, 1, "digest", "worker-a", 30)
            store.db.execute("UPDATE scope_lease SET expires_at=0 WHERE scope_id='s'")
            store.db.commit()
            self.assertEqual(store.claim_scope("s", "worker-b", ttl=30), 2)
            self.assertFalse(store.commit_job(job[0], "worker-a", job[1], 1,
                                              scope_owner="worker-a",
                                              scope_token=scope_token))
            store.close()

    def test_unit_scope_fence_rejects_before_write(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            token = store.claim_scope("s", "worker-a", ttl=30)
            store.db.execute("UPDATE scope_lease SET expires_at=0 WHERE scope_id='s'")
            store.db.commit()
            store.claim_scope("s", "worker-b", ttl=30)
            with self.assertRaises(RuntimeError):
                with store.unit_of_work("s", "worker-a", token):
                    store.record_receipt("s", 1, "ATTACHED", "h1")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0], 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
