"""R0-D closure: fenced reconciliation, first resume and lexical abstention."""
import contextlib
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.b4_frozen_replay import run
from memory_engine.engine import MemoryEngine
from tests.test_b4_frozen_fork import _write_fixture
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'store.sqlite3'
        self.store = V15Store(self.path)
        self.addCleanup(self.store.close)
        self.store.register_manifest('s', [(1, 's1'), (2, 's2'), (3, 's3')])
        self.token = self.store.claim_scope('s', 'old', ttl=30)
        self.store.record_receipt('s', 1, 'CREATED', 'h1')

    def reconcile(self, **kwargs):
        return self.store.reconcile_completed_scope(
            's', scope_owner='old', scope_token=self.token, **kwargs)

    def test_receipt_frontier_does_not_close_job_over_gap(self):
        self.store.record_receipt('s', 3, 'CREATED', 'h3')
        covered, _ = self.store.claim_job('s', 1, 1, 'a', 'old', 30)
        uncovered, _ = self.store.claim_job('s', 2, 3, 'b', 'old', 30)
        self.assertEqual(self.reconcile(), 1)
        self.assertEqual(self.store.cursor('s'), 1)
        states = dict(self.store.db.execute('SELECT job_id,status FROM consolidation_job'))
        self.assertEqual(states[covered], 'COMMITTED')
        self.assertEqual(states[uncovered], 'CLAIMED')
        self.assertEqual(self.reconcile(), 1)

    def test_stale_owner_cannot_close_successor_job_even_nested(self):
        self.store.release_scope('s', 'old', self.token)
        other = V15Store(self.path)
        self.addCleanup(other.close)
        new_token = other.claim_scope('s', 'new', ttl=30)
        job, job_token = other.claim_job('s', 1, 1, 'new-plan', 'new', 30)
        for nested in (False, True):
            with self.subTest(nested=nested):
                with self.assertRaisesRegex(RuntimeError, 'fenced'):
                    if nested:
                        with self.store.unit_of_work():
                            self.reconcile()
                    else:
                        self.reconcile()
                self.assertEqual(self.store.cursor('s'), 0)
                self.assertTrue(other.verify_lease(job, 'new', job_token))
                self.assertTrue(other.verify_scope('s', 'new', new_token))

    def test_reconcile_expiry_before_commit_rolls_back_metadata(self):
        job, _ = self.store.claim_job('s', 1, 1, 'a', 'old', 30)
        with patch.object(self.store, 'verify_scope', side_effect=[True, True, False]):
            with self.assertRaisesRegex(RuntimeError, 'fenced'):
                self.reconcile()
        self.assertEqual(self.store.cursor('s'), 0)
        self.assertEqual(self.store.db.execute(
            'SELECT status FROM consolidation_job WHERE job_id=?', (job,)).fetchone()[0], 'CLAIMED')

    def test_no_unfenced_compatibility_call(self):
        with self.assertRaises(TypeError):
            self.store.reconcile_completed_scope('s', 99)
        self.assertEqual(self.store.cursor('s'), 0)

    def test_live_lease_still_requires_owned_commit_boundary(self):
        with self.store.unit_of_work():
            with self.assertRaisesRegex(RuntimeError, 'own fenced transaction'):
                self.reconcile()
        self.assertEqual(self.store.cursor('s'), 0)


class TestFirstResume(unittest.TestCase):
    def test_process_exit_then_rebatch_closes_old_job_on_first_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            trace = _write_fixture(root)
            db = root / 'replay.sqlite3'
            code = ('from evaluation.b4_frozen_replay import run; import sys; '
                    'run(sys.argv[1],sys.argv[2],2,sys.argv[3],'
                    'crash_at="after_commit",lease_ttl=0.2)')
            child = subprocess.run([sys.executable, '-c', code, str(root), str(trace), str(db)],
                                   cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, text=True, timeout=20)
            self.assertEqual(child.returncode, 17, child.stderr)
            with contextlib.closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM source_receipt').fetchone()[0], 1)
                expiry = con.execute('SELECT expires_at FROM scope_lease').fetchone()[0]
            deadline = time.monotonic() + 3
            while time.time() <= expiry:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            first = run(str(root), str(trace), 3, str(db))
            self.assertEqual(first['messages'], 2)
            with contextlib.closing(sqlite3.connect(db)) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM consolidation_job WHERE status='CLAIMED'").fetchone()[0], 0)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM resolution_pending WHERE status='PENDING'").fetchone()[0], 1)
                self.assertEqual(con.execute('SELECT last_committed_seq FROM consolidation_cursor').fetchone()[0], 3)
            second = run(str(root), str(trace), 3, str(db))
            self.assertEqual(second['messages'], 0)
            self.assertEqual(second['diff'], {})


class TestQuestionFallback(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = V15Store(Path(self.temp.name) / 'memory.sqlite3')
        self.addCleanup(self.store.close)
        self.engine = MemoryEngine(self.store)
        self.event = self.ingest('我已确认木桥市集，我的安排是西入口。木桥市集最终确认，最终确认西入口。', 1)

    def ingest(self, text, seq, scope='s'):
        return self.engine.ingest(scope, SourceEnvelope(
            scope, seq, hashlib.sha256(text.encode()).hexdigest(), 't', text)).event_id

    def test_generic_scaffolding_does_not_inject_unrelated_memory(self):
        for query in ('我的牙科复查怎么安排？', '宠物手术安排在什么时候？',
                      '我确认了什么付款日期？', '我的安排是什么呢？'):
            with self.subTest(query=query):
                self.assertEqual(self.engine.retrieve('s', query), [])
                self.assertEqual(self.engine.build_context('s', query)['items'], [])

    def test_topic_question_retains_recall_with_distractor(self):
        self.ingest('我的安排是明天拔牙，牙科复查最终确认。', 2)
        items = self.engine.retrieve('s', '木桥市集最后定成什么了？')
        self.assertEqual([item['event_id'] for item in items], [self.event])
        self.assertEqual(len(self.engine.retrieve('s', '西入口')), 1)

    def test_fallback_preserves_scope_and_withdrawal_filter(self):
        self.ingest('木桥市集在东入口。', 1, scope='other')
        self.engine.withdraw_event('s', self.event)
        self.assertEqual(self.engine.build_context('s', '木桥市集现在怎么安排？')['items'], [])

    def test_exact_match_keeps_priority_over_partial_fallback(self):
        direct = self.ingest('木桥市集怎么安排？西入口集合。', 2)
        items = self.engine.retrieve('s', '木桥市集怎么安排？', limit=2)
        self.assertEqual([item['event_id'] for item in items], [direct, self.event])


if __name__ == '__main__':
    unittest.main()
