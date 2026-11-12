"""线路演示：固定判断只适用于随附的两条示例，不能用于实际聊天。"""
import json
import tempfile
from pathlib import Path
from astrbot_memory_input import Origin, ImportJournal
from memory_engine.native_input import NativeInputInbox, NativeInputWorker
from memory_engine.engine import MemoryEngine
from memory_engine.render import render_context
from v1_5_consolidator.store import V15Store
from v1_5_consolidator.contracts import NarrativeProvenance
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal


def fixture_judge(source, candidates, record, context):
    # 两条示例预先声明为同一经历；该函数不是通用语义判别器。
    same = bool(candidates)
    return JudgmentResult(
        source.source_seq,
        [RelationProposal(c['event_id'], 'SAME_EVENT') for c in candidates],
        suggested_action='ATTACH_EXISTING' if same else 'NEW_EVENT',
        target_event_id=candidates[0]['event_id'] if same else None,
        provenance=NarrativeProvenance('user', 'user', 'real'))


def main():
    history = json.loads(Path(__file__).with_name('history.json').read_text(encoding='utf-8'))
    origin = Origin('qq', 'demo-bot', 'demo-user', 'demo-persona', 'demo-conversation')
    with tempfile.TemporaryDirectory() as directory:
        journal = ImportJournal(Path(directory) / 'input.db')
        store = V15Store(Path(directory) / 'memory.db')
        try:
            inbox = NativeInputInbox(store)
            print('首次导入', journal.stage(history, origin))
            print('重复导入', journal.stage(history, origin))
            print('投递', journal.deliver(inbox))
            result = NativeInputWorker(inbox, fixture_judge).run_once()
            print('事件组织', result)
            assert result['created'] == result['attached'] == 1
            packet = MemoryEngine(store).build_context(origin.scope_id, '青岛海边散步')
            assert len(packet['items']) == 1
            print(render_context(packet))
        finally:
            journal.close()
            store.close()


if __name__ == '__main__':
    main()
