"""①→⑤ 离线集成回归：把"平台契约"和"端到端链路"变成会失败的断言。

与 test_plugin.py 的区别：
- `test_plugin.py` 验证插件内部行为（给定 Store 和事件，结果对不对）；
- 本文件验证**框架侧契约**（钩子是否以正确的方式注册、被调用时入参是否齐全、
  宿主缺少字段时是否静默放行、重启后积压是否续跑），并使用可观测夹具记录耗时。

它对应路线图 M1 的 L1 层；L2/L3（真实 AstrBot + NapCat + 真实 BGE）仍然未做。
"""

import asyncio
import inspect
import sys
import tempfile
import unittest

import integration_probe
import sim_astrbot as sim

MAX_HOOK_MS = 1500  # L0 验收线：端到端 P95 < 1.5 s（不含回复模型）


class TestHookContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.registry, _ = sim.load()

    def test_hooks_are_registered_with_the_assumed_filters(self):
        kinds = self.registry.kinds('capture_private')
        self.assertEqual(kinds, ['event_message_type'])
        self.assertEqual(self.registry.entries['capture_private'][0]['options'],
                         {'type': 'PRIVATE_MESSAGE'})
        self.assertEqual(self.registry.kinds('add_memory_context'), ['on_llm_request'])
        self.assertEqual(self.registry.kinds('dlm'), [], 'v1.0.18 起 /dlm 不再注册为聊天命令')

    def test_hook_signatures_match_astrbot_calling_convention(self):
        cls = self.module.DigitalMemoryStore
        self.assertTrue(inspect.iscoroutinefunction(cls.capture_private))
        self.assertTrue(inspect.iscoroutinefunction(cls.add_memory_context))
        self.assertTrue(inspect.isasyncgenfunction(cls.dlm))
        self.assertEqual(list(inspect.signature(cls.capture_private).parameters),
                         ['self', 'event'])
        self.assertEqual(list(inspect.signature(cls.add_memory_context).parameters),
                         ['self', 'event', 'req'])

    def test_plugin_does_not_import_torch_at_load_time(self):
        # 记录/查看/备份不依赖 BGE；导入插件不应触发重型依赖。
        self.assertNotIn('torch', sys.modules)
        self.assertNotIn('sentence_transformers', sys.modules)


class TestIntegrationChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix='dl-chain-')
        # 一次探针跑完四个环节，避免重复启动/重启拖慢回归。
        cls.facts = asyncio.run(integration_probe.run_probe(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_capture_only_accepts_authorised_plain_private_text(self):
        capture = self.facts['capture']
        self.assertEqual(capture['rows'], 1, '只有授权私聊的纯文本应落库')
        self.assertTrue(capture['media_chain_skipped'], '非文本链必须被拒且不猜测')
        self.assertTrue(capture['stranger_ignored'], '非白名单发送者必须被拒')
        self.assertTrue(capture['group_ignored'], '群聊消息必须被拒')
        self.assertEqual(capture['gap_records'], 0, '非纯文本跳过不应伪装成采集缺口')
        self.assertEqual(capture['unsupported'], 1, '非纯文本必须计入独立的 skipped/unsupported 计数')

    def test_injection_is_visible_within_budget_and_temporary(self):
        injection = self.facts['injection']
        self.assertEqual(injection['injected'], injection['rounds'],
                         '全部轮次都应能看到记忆片段；若为 0 说明钩子链路断开')
        self.assertTrue(injection['prefix_present'])
        self.assertTrue(injection['temporary_parts'], '注入必须是临时片段，不进入长期上下文')
        self.assertTrue(injection['budget_respected'], '载荷不得超出 context_budget_chars')

    def test_hook_latency_stays_under_l0_budget(self):
        p95 = self.facts['latency_ms']['p95']
        self.assertLess(p95, MAX_HOOK_MS,
                        '受控夹具下 P95=%.1fms；真实 BGE 需重新测' % p95)

    def test_same_request_is_not_injected_twice(self):
        dedupe = self.facts['dedupe']
        self.assertEqual(dedupe['first_parts'], 1)
        self.assertEqual(dedupe['second_parts'], 1, '同一请求重复处理不得重复追加')
        self.assertTrue(dedupe['stable'])

    def test_restart_resumes_jobs_without_duplicate_capture(self):
        restart = self.facts['restart']
        self.assertGreater(restart['pending_before_restart'], 0, '重启前应存在积压')
        self.assertTrue(restart['jobs_drained_after_restart'], '重启后积压任务必须续跑')
        self.assertEqual(restart['pending_after_drain'], 0)
        self.assertFalse(restart['duplicate_capture'], '重启后同一 message_id 不得重复采集')
        self.assertGreaterEqual(restart['injection_after_restart'], 1,
                                '重启后仍应能注入记忆')

    def test_incompatible_host_fails_open(self):
        legacy = self.facts['legacy_host']
        self.assertFalse(legacy['raised'], '宿主缺少字段时不得抛异常打断回复')
        self.assertEqual(legacy['parts'], 0)
        self.assertTrue(legacy['failed_open'])

    def test_contract_snapshot_is_part_of_the_evidence(self):
        methods = {row['method'] for row in self.facts['contract']}
        self.assertEqual(methods, {'capture_private', 'add_memory_context'})


class TestCaptureEdgeCases(unittest.TestCase):
    """采集判据的逐条契约：真实平台最可能在这些边界上"静默丢消息"。"""

    CONFIG = {'enabled': True, 'allowed_sender_ids': '42',
              'automatic_memory_enabled': True}

    def test_capture_gate_rejects_bot_self_mismatch_and_oversize(self):
        async def go(root):
            runtime = sim.SimRuntime(root, self.CONFIG)
            await runtime.start()
            try:
                await runtime.receive('正常消息', message_id='ok-1')
                await runtime.receive('机器人自己的消息', message_id='self-1',
                                      sender='42', self_id='42')
                await runtime.receive('看这个', message_id='mismatch-1',
                                      parts=[sim.Plain('链里是别的文本')])
                await runtime.receive('缺消息ID', message_id='')
                await runtime.receive('长' * 4001, message_id='long-1')
                if runtime.plugin.capture_gap_tasks:
                    await asyncio.gather(*list(runtime.plugin.capture_gap_tasks))
                rows = await runtime.scalar('SELECT COUNT(*) FROM dl_capture')
                gaps = await runtime.scalar(
                    'SELECT COALESCE(SUM(total),0) FROM dl_capture_gaps')
                contents = await runtime.query(
                    'SELECT content FROM dl_capture ORDER BY seq')
                return rows, gaps, [row[0] for row in contents], \
                    runtime.plugin.capture_skipped
            finally:
                await runtime.stop()

        with tempfile.TemporaryDirectory() as root:
            rows, gaps, contents, skipped = asyncio.run(go(root))
        self.assertEqual(rows, 1, '只有第一条正常私聊应落库')
        self.assertEqual(contents, ['正常消息'])
        self.assertEqual(skipped, 1, '超长消息计入采集跳过数')
        self.assertEqual(gaps, 1, '超长消息必须留下缺口记录，不能静默丢弃')


    def test_disabled_plugin_neither_captures_nor_injects(self):
        import asyncio

        async def go(root):
            runtime = sim.SimRuntime(root, {'enabled': False,
                                            'allowed_sender_ids': '42'})
            await runtime.start()
            try:
                await runtime.receive('我喜欢吃苹果', message_id='off-1')
                event = await runtime.receive('你之前喜欢吃苹果吗', message_id='off-2')
                result = await runtime.llm_request(event)
                rows = await runtime.scalar('SELECT COUNT(*) FROM dl_capture')
                return rows, result.count
            finally:
                await runtime.stop()

        with tempfile.TemporaryDirectory() as root:
            rows, parts = asyncio.run(go(root))
        self.assertEqual((rows, parts), (0, 0), '默认关闭时不得采集或注入')


if __name__ == '__main__':
    unittest.main()
