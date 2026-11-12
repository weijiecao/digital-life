"""原生输入宿主接入：来源映射、历史读取、串行服务、判别器接线与泵。

这些用例不启动 AstrBot：宿主对象、会话管理器和 provider 都用替身。
它们证明「契约接通」，不证明真实模型的事件归并质量。
"""
import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from astrbot_memory_input import Origin, parse_history
from memory_engine.engine import MemoryEngine
from memory_engine.native_host import (AstrBotProvider, HostInputError,
                                       NativeInputPump, NativeInputService,
                                       build_origin, event_message,
                                       history_from_conversation,
                                       journal_path_for, read_conversation,
                                       revision_for)
from memory_engine.native_judge import NativeRelationJudge, build_provider
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.gateway import MockProvider, ModelResult
from v1_5_consolidator.store import V15Store


class Sender:
    def __init__(self, user_id):
        self.user_id = user_id


class MessageObject:
    def __init__(self, message_id, user='42', group_id=None, self_id='bot'):
        self.message_id = message_id
        self.sender = Sender(user)
        self.group_id = group_id
        self.self_id = self_id


class Event:
    def __init__(self, text, *, message_id='m1', user='42', group_id=None,
                 self_id='bot', origin='webchat:FriendMessage:42'):
        self.message_str = text
        self.unified_msg_origin = origin
        self.message_obj = MessageObject(message_id, user=user, group_id=group_id,
                                         self_id=self_id)


class FakeConversation:
    def __init__(self, cid, messages, persona_id='p1'):
        self.cid = cid
        self.history = json.dumps(messages, ensure_ascii=False)
        self.persona_id = persona_id


class FakeManager:
    def __init__(self, conversation=None):
        self.conversation = conversation
        self.calls = 0

    async def get_curr_conversation_id(self, unified_msg_origin):
        self.calls += 1
        return self.conversation.cid if self.conversation else None

    async def get_conversation(self, unified_msg_origin, conversation_id):
        if self.conversation and self.conversation.cid == conversation_id:
            return self.conversation
        return None


class ScriptedProvider:
    """只回测试指定的关系标签；同时记录收到的提示词。"""
    external = False

    def __init__(self, label='DIFFERENT_EVENT', action=None):
        self.label = label
        self.action = action
        self.prompts = []

    def generate(self, prepared):
        self.prompts.append(prepared.prompt)
        ids = [item['event_id'] for item in prepared.candidates]
        relations = ([{'candidate_event_id': event_id, 'label': self.label,
                       'reason_codes': ['test']} for event_id in ids]
                     if self.label else [])
        action = self.action or ('ATTACH_EXISTING' if self.label == 'SAME_EVENT'
                                 else 'NEW_EVENT')
        payload = {
            'schema_version': 'v1.5b.1',
            'source_provenance': {'narrator': 'user', 'subject': 'user',
                                  'reality_status': 'real', 'story_id': None},
            'relations': relations,
            'suggested_action': action,
            'target_event_id': ids[0] if action == 'ATTACH_EXISTING' and ids else None,
        }
        return ModelResult(text=json.dumps(payload, ensure_ascii=False))


def count(store, table):
    return store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]


class OriginTests(unittest.TestCase):
    def test_private_event_maps_to_origin(self):
        origin = build_origin(Event('你好'), persona='p1', conversation='c1')
        self.assertEqual(origin, Origin('webchat', 'bot', '42', 'p1', 'c1'))

    def test_group_and_missing_fields_are_rejected(self):
        self.assertIsNone(build_origin(Event('hi', group_id='123'), persona='p1',
                                      conversation='c1'))
        self.assertIsNone(build_origin(Event('hi', user=None), persona='p1',
                                      conversation='c1'))
        self.assertIsNone(build_origin(Event('hi'), persona='p1'))  # 缺会话
        self.assertIsNone(build_origin(Event('hi', self_id=None, user='42'),
                                      persona='p1', conversation='c1'))
        self.assertIsNone(build_origin(Event('hi', self_id='42'), persona='p1',
                                      conversation='c1'))  # 自己发的
        self.assertIsNone(build_origin(Event('hi', origin='nocolon'), persona='p1',
                                      conversation='c1'))
        self.assertIsNone(build_origin(Event('hi'), persona='', conversation='c1'))
        self.assertIsNone(build_origin(Event('hi'), persona='p1',
                                      conversation='x' * 600))

    def test_scope_separates_users_and_streams(self):
        base = build_origin(Event('hi'), persona='p1', conversation='c1')
        other_user = build_origin(Event('hi', user='7'), persona='p1', conversation='c1')
        other_stream = build_origin(Event('hi'), persona='p1', conversation='c2')
        self.assertNotEqual(base.scope_id, other_user.scope_id)
        self.assertEqual(base.scope_id, other_stream.scope_id)
        self.assertNotEqual(base.stream_id, other_stream.stream_id)


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    def test_json_string_and_list(self):
        messages = [{'role': 'user', 'content': 'a'},
                    {'role': 'assistant', 'content': 'b'}]
        conv = FakeConversation('c1', messages)
        self.assertEqual(history_from_conversation(conv), messages)
        self.assertEqual(history_from_conversation(messages), messages)
        empty = SimpleNamespace(history='[]')
        self.assertEqual(history_from_conversation(empty), [])

    def test_invalid_payloads(self):
        for bad in (SimpleNamespace(history='not json'),
                    SimpleNamespace(history='{"role": "user"}'),
                    SimpleNamespace(history=None)):
            with self.subTest(bad=bad), self.assertRaises(HostInputError):
                history_from_conversation(bad)

    async def test_read_conversation(self):
        conversation = FakeConversation('c1', [{'role': 'user', 'content': 'a'}])
        cid, messages = await read_conversation(FakeManager(conversation), 'u:1')
        self.assertEqual(cid, 'c1')
        self.assertEqual(messages[0]['content'], 'a')
        self.assertEqual(await read_conversation(FakeManager(), 'u:1'), (None, None))
        self.assertEqual(await read_conversation(object(), 'u:1'), (None, None))

    def test_event_message_fallback(self):
        message = event_message(Event('单条', message_id='m9'))
        self.assertEqual(message, [{'role': 'user', 'content': '单条',
                                    'message_id': 'm9'}])
        self.assertIsNone(event_message(Event('  ')))
        self.assertIsNone(event_message(Event('x', message_id=None)))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = V15Store(self.path / 'engine.db')
        self.addCleanup(self.store.close)
        self.origin = Origin('webchat', 'bot', '42', 'p1', 'c1')

    def service(self, judge=None):
        service = NativeInputService(self.store, journal_path_for(self.path),
                                     judge=judge,
                                     judge_revision=revision_for(judge, 'none'))
        self.addCleanup(service.close)
        return service

    def test_stage_and_deliver(self):
        service = self.service()
        history = [{'role': 'user', 'content': '周六去青岛。'},
                   {'role': 'assistant', 'content': '记得看天气。'}]
        first = service.submit(history, self.origin)
        self.assertEqual(first['status'], 'staged')
        self.assertEqual((first['new'], first['duplicates']), (2, 0))
        self.assertEqual(first['delivered'], {'acknowledged': 2, 'remaining': 0})
        self.assertEqual(service.counts(), {'QUEUED': 1, 'ARCHIVED': 1})
        again = service.submit(history, self.origin)
        self.assertEqual((again['new'], again['duplicates']), (0, 2))

    def test_conflict_is_reported_not_partially_written(self):
        service = self.service()
        history = [{'role': 'user', 'content': 'a'},
                   {'role': 'user', 'content': 'b'}]
        service.submit(history, self.origin)
        result = service.submit(history[:1], self.origin)
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(service.stats['conflicts'], 1)
        self.assertEqual(service.last_conflict['stream_id'], self.origin.stream_id)
        self.assertEqual(count(self.store, 'native_input_inbox'), 2)

    def test_invalid_history_is_reported(self):
        service = self.service()
        result = service.submit([{'role': 'mystery', 'content': 'x'}], self.origin)
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(service.stats['invalid'], 1)

    def test_without_judge_nothing_is_created(self):
        service = self.service()
        service.submit([{'role': 'user', 'content': '周六去青岛。'}], self.origin)
        self.assertEqual(service.run_once(), {'skipped': 'no_judge'})
        self.assertEqual(count(self.store, 'event_candidate'), 0)
        self.assertEqual(service.counts(), {'QUEUED': 1})

    def test_judge_creates_event_and_query_finds_it(self):
        provider = ScriptedProvider()
        service = self.service(NativeRelationJudge(self.store, provider))
        service.submit([{'role': 'assistant', 'content': '建议看看天气。'},
                        {'role': 'user', 'content': '周六去青岛海边散步。'}],
                       self.origin)
        self.assertEqual(service.run_once()['created'], 1)
        packet = MemoryEngine(self.store).build_context(self.origin.scope_id,
                                                        '青岛海边散步')
        self.assertEqual(len(packet['items']), 1)
        self.assertIn('周六去青岛海边散步', packet['items'][0]['evidence'])
        self.assertEqual(packet['items'][0]['admission']['verdict'], 'SUPPORTED')
        # 带角色的上下文进入判别提示词，但只作为指代材料。
        self.assertIn('assistant: 建议看看天气。', provider.prompts[0])

    def test_judge_defers_when_candidates_are_not_covered(self):
        first = self.service(NativeRelationJudge(self.store, ScriptedProvider()))
        self.assertEqual(first.submit([{'role': 'user', 'content': '周六去青岛海边散步。'}],
                                      self.origin)['status'], 'staged')
        self.assertEqual(first.run_once()['created'], 1)
        first.submit([{'role': 'user', 'content': '周六去青岛海边散步。'},
                      {'role': 'user', 'content': '青岛海边的栈桥也去了。'}], self.origin)
        dropping = self.service(NativeRelationJudge(
            self.store, ScriptedProvider(label=None)))
        self.assertEqual(dropping.run_once()['deferred'], 1)
        self.assertEqual(count(self.store, 'event_candidate'), 1)

    def test_judge_budget_defers_instead_of_truncating(self):
        judge = NativeRelationJudge(self.store, ScriptedProvider(), max_chars=300)
        record = parse_history([{'role': 'user', 'content': '很长' * 400}],
                               self.origin)[0]
        source = SourceEnvelope(record.scope_id, 1, record.content_hash,
                                record.recorded_at or '', record.text)
        judgment = judge(source, [{'event_id': 'evt_1', 'title': 't',
                                   'evidence': ['e']}], record, [])
        self.assertTrue(judgment.issues[0].startswith('prompt:'))
        self.assertEqual(judgment.source_seq, 1)

    def test_judge_cools_down_after_repeated_failures(self):
        class Boom(ScriptedProvider):
            def generate(self, prepared):
                raise RuntimeError('provider down')
        judge = NativeRelationJudge(self.store, Boom(), failure_threshold=2,
                                    cooldown_seconds=60)
        record = parse_history([{'role': 'user', 'content': 'a'}], self.origin)[0]
        source = SourceEnvelope(record.scope_id, 1, record.content_hash, '', record.text)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                judge(source, [], record, [])
        with self.assertRaises(RuntimeError):
            judge(source, [], record, [])  # 冷却期直接拒绝，不再外发


class ProviderTests(unittest.TestCase):
    def test_factory(self):
        self.assertIsNone(build_provider('none'))
        self.assertIsInstance(build_provider('mock'), MockProvider)
        with self.assertRaises(ValueError):
            build_provider('openai', model='m')
        with self.assertRaises(ValueError):
            build_provider('mystery')

    def test_astrbot_provider_uses_host_loop(self):
        class HostContext:
            def __init__(self):
                self.calls = []

            async def llm_generate(self, chat_provider_id, prompt):
                self.calls.append((chat_provider_id, prompt))
                return SimpleNamespace(completion_text='{"ok": true}')

        async def scenario():
            context = HostContext()
            provider = AstrBotProvider(context, 'prov-1',
                                       asyncio.get_running_loop(), timeout=5)
            result = await asyncio.to_thread(
                provider.generate, SimpleNamespace(prompt='只输出 JSON'))
            return result, context

        result, context = asyncio.run(scenario())
        self.assertEqual(result.text, '{"ok": true}')
        self.assertEqual(result.model, 'prov-1')
        self.assertEqual(context.calls, [('prov-1', '只输出 JSON')])

    def test_astrbot_provider_requires_llm_generate(self):
        with self.assertRaises(ValueError):
            AstrBotProvider(object(), 'p', None)

    def test_revision_changes_with_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            store = V15Store(Path(temp) / 'engine.db')
            try:
                judge = NativeRelationJudge(store, ScriptedProvider())
                longer = NativeRelationJudge(store, ScriptedProvider(), context_limit=4)
                self.assertNotEqual(revision_for(judge), revision_for(longer))
                self.assertEqual(revision_for(None, 'fallback'), 'fallback')
            finally:
                store.close()


class PumpTests(unittest.TestCase):
    def test_pump_runs_one_tick_on_owner_thread(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name)
        origin = Origin('webchat', 'bot', '42', 'p1', 'c1')
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown, wait=True)

        def build():
            store = V15Store(path / 'engine.db')
            service = NativeInputService(
                store, journal_path_for(path),
                judge=NativeRelationJudge(store, ScriptedProvider()))
            service.submit([{'role': 'user', 'content': '周六去青岛海边散步。'}],
                           origin)
            return store, service

        store, service = pool.submit(build).result()
        pump = NativeInputPump(service, executor=pool, interval=0.1,
                               retry_every=0, limit=5)

        async def scenario():
            pump.start()
            await asyncio.sleep(0.35)
            await pump.stop()
            await pump.stop()

        try:
            asyncio.run(scenario())
            self.assertEqual(pool.submit(count, store, 'event_candidate').result(), 1)
        finally:
            def close():
                service.close()
                store.close()

            pool.submit(close).result()


if __name__ == '__main__':
    unittest.main()
