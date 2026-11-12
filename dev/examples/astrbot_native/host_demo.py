"""宿主接入线路演示：会话历史 → 原生输入 → 判别器 → 引擎检索。

用替身会话管理器和脚本化 provider，不需要 AstrBot、模型账号或网络。
脚本化 provider 只按演示剧本给标签，不能当作真实判别器。
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

DEV = Path(__file__).resolve().parents[2]
if str(DEV) not in sys.path:
    sys.path.insert(0, str(DEV))

from memory_engine.engine import MemoryEngine
from memory_engine.native_host import (NativeInputService, build_origin,
                                       journal_path_for, read_conversation)
from memory_engine.native_judge import NativeRelationJudge
from memory_engine.render import render_context
from v1_5_consolidator.gateway import ModelResult
from v1_5_consolidator.store import V15Store


class Conversation:
    def __init__(self, cid, messages):
        self.cid = cid
        self.history = json.dumps(messages, ensure_ascii=False)


class Manager:
    def __init__(self, conversation):
        self.conversation = conversation

    async def get_curr_conversation_id(self, unified_msg_origin):
        return self.conversation.cid

    async def get_conversation(self, unified_msg_origin, conversation_id):
        return self.conversation


class Event:
    def __init__(self, text):
        self.message_str = text
        self.unified_msg_origin = 'qq:FriendMessage:demo-user'
        self.message_obj = type('MessageObject', (), {
            'sender': type('Sender', (), {'user_id': 'demo-user'})(),
            'group_id': None, 'self_id': 'demo-bot', 'message_id': 'demo-1'})()


class ScriptedProvider:
    """演示剧本：有候选就并入同一事件，没有就新建。"""
    external = False

    def generate(self, prepared):
        ids = [item['event_id'] for item in prepared.candidates]
        payload = {
            'schema_version': 'v1.5b.1',
            'source_provenance': {'narrator': 'user', 'subject': 'user',
                                  'reality_status': 'real', 'story_id': None},
            'relations': [{'candidate_event_id': event_id, 'label': 'SAME_EVENT',
                           'reason_codes': ['demo']} for event_id in ids],
            'suggested_action': 'ATTACH_EXISTING' if ids else 'NEW_EVENT',
            'target_event_id': ids[0] if ids else None,
        }
        return ModelResult(text=json.dumps(payload, ensure_ascii=False))


async def main():
    conversation = Conversation('c1', [
        {'role': 'user', 'content': '周六去青岛海边散步。'},
        {'role': 'assistant', 'content': '记得看看天气。'},
        {'role': 'user', 'content': '青岛海边散步还去了栈桥。'},
    ])
    origin = build_origin(Event('青岛海边散步还去了栈桥。'), persona='demo-persona',
                          conversation=conversation.cid)
    with tempfile.TemporaryDirectory() as directory:
        store = V15Store(Path(directory) / 'memory.db')
        try:
            service = NativeInputService(store, journal_path_for(directory),
                                         judge=NativeRelationJudge(store, ScriptedProvider()))
            cid, history = await read_conversation(Manager(conversation), 'demo')
            print('读取历史', cid, len(history), '条')
            print('提交', service.submit(history, origin))
            print('判别', service.run_once())
            print('状态', service.counts())
            packet = MemoryEngine(store).build_context(origin.scope_id, '青岛海边散步')
            print('检索事件数', len(packet['items']))
            print(render_context(packet))
            service.close()
        finally:
            store.close()


if __name__ == '__main__':
    asyncio.run(main())
