"""离线演示原生输入→记忆基座→预算输出→模拟宿主。无需模型账号。"""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from astrbot_memory_input import Origin, ImportJournal
from memory_engine.native_input import NativeInputInbox, NativeInputWorker
from memory_engine.engine import MemoryEngine
from memory_engine.output import MemoryOutputService
from memory_engine.token_budget import TokenCounter
from memory_output import AstrBotOutputAdapter, OutputRequest
from v1_5_consolidator.store import V15Store
from v1_5_consolidator.contracts import NarrativeProvenance
from v1_5_consolidator.relation_judge import JudgmentResult


class DemoPart:
    def __init__(self, text):
        self.text = text
        self._no_save = False
    def mark_as_temp(self):
        self._no_save = True
        return self


def fixture_judge(source, candidates, record, context):
    # 仅一条已知示例，预先声明为用户真实经历；不是通用语义判断。
    assert not candidates
    return JudgmentResult(source.source_seq, suggested_action='NEW_EVENT',
                          provenance=NarrativeProvenance('user', 'user', 'real'))


def main():
    with tempfile.TemporaryDirectory() as directory:
        store = V15Store(Path(directory) / 'memory.db')
        journal = ImportJournal(Path(directory) / 'input.db')
        try:
            origin = Origin('qq', 'demo-bot', 'demo-user', 'demo-persona', 'demo-conversation')
            journal.stage([{'role':'user','content':'周六去青岛海边散步，带了风筝。'},
                           {'role':'assistant','content':'记得看看天气。'}], origin)
            inbox = NativeInputInbox(store)
            journal.deliver(inbox)
            NativeInputWorker(inbox, fixture_judge).run_once()
            engine = MemoryEngine(store)
            service = MemoryOutputService(engine, TokenCounter(), AstrBotOutputAdapter(DemoPart))
            request = OutputRequest('demo-request-1', origin.scope_id, '青岛海边散步', 512)
            host = SimpleNamespace(prompt=request.query, system_prompt='保留原有人格设定',
                                   contexts=[], extra_user_content_parts=[])
            output = service.prepare(request)
            assert service.inject(host, output, request).status == 'INJECTED'
            assert service.inject(host, output, request).status == 'UNCHANGED'
            assert len(host.extra_user_content_parts) == 1
            assert host.extra_user_content_parts[0]._no_save
            print(json.dumps({'items':len(output.items), 'tokens':output.used_tokens,
                              'budget':output.budget_tokens, 'temporary':True,
                              'repeated_injection_blocks':len(host.extra_user_content_parts)}, ensure_ascii=False))
            print(output.text)
            engine.delete_source(origin.scope_id, 1)
            stale = service.inject(host, output, request)
            assert stale.status == 'REJECTED' and not host.extra_user_content_parts
            print('来源删除后：拒绝旧输出并移除先前记忆块。')
        finally:
            journal.close()
            store.close()

if __name__ == '__main__':
    main()
