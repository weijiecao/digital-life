import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.store import V15Store


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
