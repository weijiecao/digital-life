"""独立离线评估：虚构数据与固定预期，仅衡量指定场景的上下文供给。"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from astrbot_plugin_digital_memory_store.storage import Store


class FixtureEncoder:
    model_key = 'offline-scenario-fixture'
    def __init__(self, path): pass
    def encode_documents(self, texts):
        return [[1.0, 0.0] if ('书店计划' in text or '原计划周六' in text) else [0.0, 1.0] for text in texts]
    def encode_queries(self, texts): return [[1.0, 0.0] for _ in texts]


# 每条预期由人工指定；这些例子并非自然聊天样本。
CASES = [
    dict(id='named_event', query='书店计划怎么样', expect=['原计划周六去书店', '改到周日去书店'], relation=True),
    dict(id='other_event', query='登山计划怎么样', expect=['计划一月去登山'], forbid=['去书店']),
    dict(id='unknown_event', query='海边旅行怎么样', empty=True),
    dict(id='wrong_scope', query='书店计划怎么样', other_scope=True, empty=True),
    dict(id='two_events_disabled', query='书店计划和登山计划怎么样', empty=True),
    dict(id='two_events_enabled', query='书店计划和登山计划怎么样', multi=True, expect=['原计划周六去书店', '改到周日去书店', '计划一月去登山'], relation=True, two=True),
    dict(id='small_budget', query='书店计划怎么样', budget=150, empty=True),
    dict(id='future_boundary', query='书店计划怎么样', as_of='2025-01-02T00:00:00+00:00', empty=True),
    dict(id='unrelated_statement', query='今天天气怎么样', empty=True),
    dict(id='negative_comparison', query='书店计划不是登山计划', multi=True, forbid=['计划一月去登山']),
    # 探索自然改写：预期提供相关原话，失败也是需要保留的结果。
    dict(id='paraphrase_book', query='之前买书的安排是什么', expect=['改到周日去书店']),
    # 没有前文和有效线索时，无法确定指代；保守不注入是正确边界。
    dict(id='implicit_reference', query='那个安排后来改到哪天了', empty=True),
    dict(id='split_clues_enabled', query='周六书店安排怎么样', weak=True,
         expect=['原计划周六去书店', '改到周日去书店'], relation=True, unconfirmed=True),
    dict(id='split_clues_disabled', query='周六书店安排怎么样', empty=True),
    dict(id='semantic_fixture', query='之前买书的安排是什么', semantic=True,
         expect=['原计划周六去书店', '改到周日去书店'], relation=True, unconfirmed=True),
]


def evaluate(case, store, scopes):
    budget = case.get('budget', 800)
    store.multi_event_context_enabled = case.get('multi', False)
    store.weak_association_enabled = case.get('weak', False)
    store.association_context_enabled = case.get('weak', False)
    store.semantic_source_context_enabled = case.get('semantic', False)
    store.model_dir = (case.get('semantic_dir', 'fixture-only')
                       if case.get('semantic') else '')
    as_of = datetime.fromisoformat(case['as_of']) if case.get('as_of') else datetime(2025, 2, 1, tzinfo=timezone.utc)
    packet = store.context_packet(scopes[bool(case.get('other_scope'))], case['query'], budget=budget, as_of=as_of)
    errors = []
    if len(packet) > budget: errors.append('超出字符预算')
    if case.get('empty') and packet: errors.append('预期不提供上下文')
    for text in case.get('expect', []):
        if text not in packet: errors.append('缺少指定完整原话：' + text)
    for text in case.get('forbid', []):
        if text in packet: errors.append('包含不应提供的内容：' + text)
    if case.get('relation'):
        try:
            data = json.loads(packet)
            if '关联历史' in data: data = data['关联历史']
            groups = data.get('多个事件', [data])
            if not any(r.get('类型') == '修正' for group in groups for r in group.get('关系', [])):
                errors.append('缺少修正关系')
        except (ValueError, AttributeError, TypeError): errors.append('无法读取关系结构')
    if case.get('two'):
        try:
            if len(json.loads(packet).get('多个事件', [])) != 2: errors.append('未提供两个独立事件')
        except (ValueError, AttributeError, TypeError): errors.append('无法读取多事件结构')
    if case.get('unconfirmed') and '未确认的联想事件' not in packet:
        errors.append('缺少未确认联想标记')
    return {'id': case['id'], 'passed': not errors, 'errors': errors, 'characters': len(packet)}


def _factory(model_dir):
    if not model_dir:
        return FixtureEncoder
    from astrbot_plugin_digital_memory_store.engine.e32.cpu_model import CPUSentenceEmbedder
    return lambda path: CPUSentenceEmbedder(path, cpu_threads=4, batch_size=8)


def run(model_dir=''):
    """model_dir 为空时用受控替身；给出本地模型目录时用真实 BGE 跑同一批场景。"""
    with tempfile.TemporaryDirectory(prefix='memory-eval-') as d:
        store = Store(d, model_dir, _factory(model_dir))
        try:
            scope, other = store.scope('fixture-a', 'private'), store.scope('fixture-b', 'private')
            old = store.save(scope, '书店计划', '原计划周六去书店')
            new = store.save(scope, '书店计划', '改到周日去书店')
            climb = store.save(scope, '登山计划', '计划一月去登山')
            store.save(other, '其他计划', '其他会话的私有内容')
            for mid, stamp in [(old, '2025-01-01T00:00:00+00:00'), (new, '2025-01-02T00:00:00+00:00'), (climb, '2025-01-01T00:00:00+00:00')]:
                store.flow.db.execute('UPDATE messages SET recorded_at=? WHERE id=?', (stamp, mid))
            store.flow.db.commit()
            store.relate_sources(scope, new, old, 'corrects')
            results = []
            for case in CASES:
                if case.get('semantic') and model_dir:
                    # 真实 BGE 下"语义改写"场景要检验的就是真实模型，不用替身向量。
                    case = {**case, 'semantic_dir': model_dir}
                results.append(evaluate(case, store, (scope, other)))
        finally:
            store.close()
    limitation = ('固定虚构场景；未调用真实模型或平台；不代表自然对话准确率。' if not model_dir
                  else '固定虚构场景；真实 BGE 编码，但仍非自然对话样本。')
    return {'suite': 'synthetic-context-v1' if not model_dir else 'synthetic-context-real-bge-v1',
            'total': len(results), 'passed': sum(x['passed'] for x in results),
            'results': results, 'model_dir': model_dir, 'limitations': limitation}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', default='',
                        help='本地 SentenceTransformer 目录；给出后语义场景用真实 BGE')
    parser.add_argument('--out', default='', help='把结果 JSON 写到指定文件')
    args = parser.parse_args()
    report = run(args.model_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
        print('written', args.out)
    else:
        print(text)
    sys.exit(0 if report['passed'] == report['total'] else 1)
