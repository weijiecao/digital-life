"""F1 回归：模型加载不得占住采集/整理用的单线程池。

背景（REAL_ENV_REPORT F1）：bge-small-zh 冷加载实测 11–13 s；原实现是在单线程池里
懒加载，加载期间采集与整理全部排队，导致前几轮拿不到记忆。
修复：Store.prewarm_embedder() 在独立线程加载；加载中语义路径立即失败降级为字面。
"""

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

import sim_astrbot as sim
from .test_store import FakeEncoder

from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
from astrbot_plugin_digital_memory_store.storage import Store


class SlowEncoder:
    """模拟真实 BGE 的加载耗时（0.4 s），编码本身很快。"""

    model_key = 'slow-prewarm-fixture'
    load_seconds = 0.4

    def __init__(self, path):
        time.sleep(SlowEncoder.load_seconds)

    def encode_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def encode_queries(self, texts):
        return [[1.0, 0.0] for _ in texts]


class TestPrewarmEmbedder(unittest.TestCase):
    def test_prewarm_runs_off_pool_and_capture_stays_fast(self):
        with tempfile.TemporaryDirectory() as d:
            model_dir = Path(d) / 'fake-model'
            model_dir.mkdir()
            store = Store(d, str(model_dir), SlowEncoder)
            try:
                self.assertEqual(store.embedder_state, 'idle')
                self.assertEqual(store.prewarm_embedder(), 'loading')
                self.assertFalse(store.embedder_ready)

                started = time.perf_counter()
                scope = store.scope('1', 's')
                self.assertTrue(store.capture(scope, 'm1', '计划去青岛海边散步',
                                              recorded_at='2025-01-01T12:00:00+00:00'))
                process_jobs(store.flow.db)
                elapsed = time.perf_counter() - started
                self.assertLess(elapsed, 0.2, '加载期间采集/整理不得被阻塞')

                # 语义路径在加载中必须快速失败，而不是等模型
                with self.assertRaises(ValueError) as ctx:
                    store._index()
                self.assertIn('embedder_loading', str(ctx.exception))

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not store.embedder_ready:
                    time.sleep(0.02)
                self.assertTrue(store.embedder_ready)
                self.assertEqual(store.embedder_state, 'ready')
                self.assertTrue(store._index().model_key.startswith('slow-prewarm-fixture'))
            finally:
                store.close()

    def test_prewarm_is_idempotent_and_thread_is_daemon(self):
        with tempfile.TemporaryDirectory() as d:
            model_dir = Path(d) / 'fake-model'
            model_dir.mkdir()
            store = Store(d, str(model_dir), SlowEncoder)
            try:
                self.assertEqual(store.prewarm_embedder(), 'loading')
                self.assertEqual(store.prewarm_embedder(), 'loading', '重复调用不得再起线程')
                thread = store._prewarm_thread
                self.assertIsNotNone(thread)
                self.assertTrue(thread.daemon, '预热线程不能阻止进程退出')
                self.assertIsInstance(thread, threading.Thread)
                thread.join(5)
                self.assertTrue(store.embedder_ready)
            finally:
                store.close()

    def test_prewarm_skipped_for_fixture_path_and_lazy_load_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, 'fixture-only', FakeEncoder)
            try:
                # 替身路径不是真实目录：不预热，保持原有懒加载语义
                self.assertEqual(store.prewarm_embedder(), 'idle')
                self.assertEqual(store.embedder_state, 'idle')
                self.assertTrue(store._index().model_key.startswith('fixture-only'))
                self.assertTrue(store.embedder_ready)
            finally:
                store.close()

    def test_prewarm_failure_is_recorded_and_does_not_block_capture(self):
        def broken(_path):
            raise RuntimeError('model files corrupt')

        with tempfile.TemporaryDirectory() as d:
            model_dir = Path(d) / 'broken-model'
            model_dir.mkdir()
            store = Store(d, str(model_dir), broken)
            try:
                self.assertEqual(store.prewarm_embedder(), 'loading')
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and store.embedder_state == 'loading':
                    time.sleep(0.02)
                self.assertEqual(store.embedder_state, 'failed')
                self.assertFalse(store.embedder_ready)
                scope = store.scope('1', 's')
                self.assertTrue(store.capture(scope, 'm1', '原计划周六去书店',
                                              recorded_at='2025-01-01T12:00:00+00:00'))
                with self.assertRaises(RuntimeError):
                    store._index()
            finally:
                store.close()


class TestModelStateVisible(unittest.TestCase):
    def test_context_status_reports_model_state(self):
        async def go(root):
            runtime = sim.SimRuntime(root, {'enabled': True, 'allowed_sender_ids': '42'})
            await runtime.plugin.initialize()
            try:
                event = await runtime.receive('/dlm 上下文状态')
                return [item async for item in runtime.plugin.dlm(event)]
            finally:
                await runtime.stop()

        with tempfile.TemporaryDirectory() as root:
            results = asyncio.run(go(root))
        self.assertTrue(results)
        self.assertIn('本地模型：', results[0])


if __name__ == '__main__':
    unittest.main()
