"""后台整理任务的失败策略：重试上限、死信状态、不阻塞同会话后续任务。

对应核查报告 P2-7：`attempts` 无上限，长期失败任务会每 5 分钟空转一次。
死信只停止自动重试，不删除原话，也不改变"未确认"语义。
"""

import asyncio
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import sim_astrbot as sim
from test_store import FakeEncoder

from astrbot_plugin_digital_memory_store.auto_organize import (
    MAX_JOB_ATTEMPTS, organize_recent, process_jobs)
from astrbot_plugin_digital_memory_store.storage import Store

PATCH = 'astrbot_plugin_digital_memory_store.auto_organize.organize_recent'


def clear_backoff(db, status='pending'):
    db.execute('UPDATE dl_auto_jobs SET next_at=\'\' WHERE status=?', (status,))
    db.commit()


class TestAutoJobFailurePolicy(unittest.TestCase):
    def test_failed_job_stops_retrying_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, '', FakeEncoder)
            try:
                scope = store.scope('1', 's')
                store.capture(scope, '1', '计划去青岛海边散步')
                with patch(PATCH, side_effect=RuntimeError('boom')):
                    for _ in range(MAX_JOB_ATTEMPTS):
                        clear_backoff(store.flow.db)
                        process_jobs(store.flow.db)
                row = store.flow.db.execute(
                    'SELECT status,attempts,next_at FROM dl_auto_jobs').fetchone()
                self.assertEqual(row['status'], 'dead')
                self.assertEqual(row['attempts'], MAX_JOB_ATTEMPTS)
                self.assertEqual(row['next_at'], '', '死信不应再排定重试时间')
                with patch(PATCH) as retry:
                    process_jobs(store.flow.db)
                self.assertEqual(retry.call_count, 0, '死信后不得继续空转')
                self.assertEqual(store.flow.db.execute(
                    'SELECT COUNT(*) FROM dl_capture').fetchone()[0], 1,
                    '死信只停自动整理，原话必须保留')
                self.assertEqual(store.auto_job_health(scope)['dead'], 1)
            finally:
                store.close()

    def test_dead_job_does_not_block_later_sources_in_same_scope(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, '', FakeEncoder)
            try:
                scope = store.scope('1', 's')
                store.capture(scope, '1', '青岛海边散步')
                store.capture(scope, '2', '青岛海边散步之后去吃饭')

                def fail_first(db, scope_, *, through_seq=None, **kwargs):
                    if through_seq == 1:
                        raise RuntimeError('boom')
                    return organize_recent(db, scope_, through_seq=through_seq, **kwargs)

                with patch(PATCH, side_effect=fail_first):
                    for _ in range(MAX_JOB_ATTEMPTS):
                        clear_backoff(store.flow.db)
                        process_jobs(store.flow.db)
                    # 死信后同会话的后续任务必须继续，否则一条坏数据会冻住整个会话。
                    clear_backoff(store.flow.db)
                    process_jobs(store.flow.db)
                status = {row['seq']: row['status'] for row in store.flow.db.execute(
                    'SELECT seq,status FROM dl_auto_jobs')}
                self.assertEqual(status, {1: 'dead', 2: 'done'})
            finally:
                store.close()

    def test_dead_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, '', FakeEncoder)
            scope = store.scope('1', 's')
            store.capture(scope, '1', '计划去青岛海边散步')
            with patch(PATCH, side_effect=RuntimeError('boom')):
                for _ in range(MAX_JOB_ATTEMPTS):
                    clear_backoff(store.flow.db)
                    process_jobs(store.flow.db)
            store.close()
            reopened = Store(d, '', FakeEncoder)
            try:
                process_jobs(reopened.flow.db)
                row = reopened.flow.db.execute(
                    'SELECT status,attempts FROM dl_auto_jobs').fetchone()
                self.assertEqual((row['status'], row['attempts']),
                                 ('dead', MAX_JOB_ATTEMPTS), '重启不得复活死信任务')
                self.assertEqual(reopened.flow.db.execute(
                    'SELECT COUNT(*) FROM dl_auto_jobs').fetchone()[0], 1,
                    '重启不得为同一原话重复建任务')
            finally:
                reopened.close()


class TestDeadLetterVisibility(unittest.TestCase):
    def test_memory_status_command_reports_dead_jobs(self):
        content = '计划去青岛海边散步'
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()

        async def go(root):
            runtime = sim.SimRuntime(root, {'enabled': True, 'allowed_sender_ids': '42'})
            await runtime.plugin.initialize()
            try:
                # store 尚未创建，先在池线程保证存在，再写入一条死信任务。
                await runtime.query('SELECT 1')
                scope = runtime.plugin.store.scope('42', 'test:FriendMessage:42')
                await runtime.execute(
                    'INSERT INTO dl_capture(seq,scope,event_hash,content,content_sha,recorded_at,state)'
                    " VALUES(1,?,?,?,?,'2025-01-01T00:00:00+00:00','pending')",
                    (scope, 'h1', content, digest))
                await runtime.execute(
                    "INSERT INTO dl_auto_jobs(scope,seq,status,attempts,next_at)"
                    " VALUES(?,1,'dead',8,'')", (scope,))
                event = await runtime.command('/dlm 记忆状态')
                return list(event.results)
            finally:
                await runtime.stop()

        with tempfile.TemporaryDirectory() as root:
            results = asyncio.run(go(root))
        self.assertTrue(results)
        self.assertIn('整理失败任务 1 条', results[0])
        self.assertIn('原话仍保留', results[0])


if __name__ == '__main__':
    unittest.main()
