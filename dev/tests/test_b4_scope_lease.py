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

    def test_receipt_rolls_back_with_unit(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            with self.assertRaises(RuntimeError):
                with store.unit_of_work():
                    store.record_receipt("s", 1, "ATTACHED", "h1")
                    raise RuntimeError("crash")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0], 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
