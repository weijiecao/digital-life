"""上游基线检索层对齐：把 3 条冻结旧来源导入下游 Store，逐案例记录召回结果。

不预设通过/失败：只输出"哪一轮召回了什么、是否泄漏 held-out 细节"的差异表。
语义路径（真实 BGE）未接入，本脚本只覆盖规则/字面路径。

用法：python eval_baseline_retrieval.py
输出：reports/baseline_retrieval.json + 控制台表格
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from astrbot_plugin_digital_memory_store.storage import Store

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / 'fixtures'
AS_OF = datetime(2026, 11, 4, tzinfo=timezone.utc)
BUDGET = 800


class _UnusedEncoder:
    model_key = 'no-model'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        raise RuntimeError('semantic path disabled for this harness')

    def encode_queries(self, texts):
        raise RuntimeError('semantic path disabled for this harness')


class FixtureSemanticEncoder:
    """受控语义替身：只验证"语义管线是否接上"，不代表真实 BGE 的准确率。

    四维正交：书店 / 足球 / 电影 / 无关。真实模型到位后应替换为 CPUSentenceEmbedder。
    """
    model_key = 'fixture-baseline-4d'

    def __init__(self, path):
        pass

    @staticmethod
    def _vec(text):
        if '书店' in text or '编程资料' in text:
            return [1.0, 0.0, 0.0, 0.0]
        if '足球' in text:
            return [0.0, 1.0, 0.0, 0.0]
        if '电影' in text:
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]

    def encode_documents(self, texts):
        return [self._vec(t) for t in texts]

    def encode_queries(self, texts):
        return [self._vec(t) for t in texts]


def load():
    cases = json.loads((FIXTURES / 'cases_fixture.json').read_text(encoding='utf-8'))['cases']
    snapshot = json.loads((FIXTURES / 'reviewed_fixture.json').read_text(encoding='utf-8'))
    # 期望片段取旧来源正文中「】」之后、「；」之前的部分，避免把 held-out 细节当成召回目标。
    snippets = {}
    for event in snapshot['events']:
        for source in event['sources']:
            body = source['content'].split('】', 1)[-1].split('；')[0]
            snippets[source['external_source_id']] = body
    for case in cases:
        source_id = case.get('expected_old_source_id')
        case['expected_snippet'] = snippets.get(source_id) if source_id else None
    return cases, snapshot


def seed(store, snapshot):
    """导入冻结旧来源；记录时间使用夹具声明的时间，保证早于当前请求。"""
    scope = store.scope('baseline-user', 'private')
    for event in snapshot['events']:
        for source in event['sources']:
            message_id = store.save(scope, event['title'], source['content'])
            store.flow.db.execute(
                'UPDATE messages SET recorded_at=? WHERE id=?',
                (source['recorded_at'], message_id))
            # 每次更新时间后提交，避免隐式事务挡住下一次 save 的 BEGIN IMMEDIATE。
            store.flow.db.commit()
    return scope


def run_mode(store, scope, cases, mode):
    store.weak_association_enabled = mode.startswith('weak')
    store.semantic_source_context_enabled = mode.startswith('semantic')
    rows = []
    for case in cases:
        focus = None
        turns = []
        for index, clue in enumerate(case['clues'], 1):
            if mode.endswith('followup'):
                packet, focus = store.context_turn(
                    scope, clue, focus, budget=BUDGET, as_of=AS_OF)
            else:
                packet = store.context_packet(scope, clue, budget=BUDGET, as_of=AS_OF)
            expected = case.get('expected_snippet')
            turns.append({
                'turn': index,
                'clue': clue,
                'chars': len(packet),
                'hit': bool(expected and expected in packet),
                # held-out 细节只对负例算泄漏：正例召回原话时它本来就该出现。
                'leak': bool(case.get('expected_snippet') is None and case.get('old_only_detail')
                             and case['old_only_detail'] in packet),
                'unconfirmed_mark': '未确认' in packet,
            })
        rows.append({
            'case_id': case['case_id'],
            'expected': case.get('expected_snippet'),
            'expected_empty': case.get('expected_snippet') is None,
            'turns': turns,
            'called': any(t['chars'] for t in turns),
            'any_hit': any(t['hit'] for t in turns),
            'any_leak': any(t['leak'] for t in turns),
        })
    return rows


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='上游基线 4 案例检索对齐')
    parser.add_argument('--model-dir', default=None,
                        help='本地 BGE 目录（SentenceTransformer 格式）；提供后追加 semantic_real 模式')
    args = parser.parse_args(argv)

    cases, snapshot = load()
    report = {'suite': 'baseline-retrieval-alignment-v1', 'as_of': AS_OF.isoformat(),
              'budget': BUDGET, 'modes': {}}
    with tempfile.TemporaryDirectory() as rule_dir, tempfile.TemporaryDirectory() as sem_dir:
        rule = Store(rule_dir, '', _UnusedEncoder)
        semantic = Store(sem_dir, 'fixture-baseline-4d', FixtureSemanticEncoder)
        real = None
        try:
            rule_scope = seed(rule, snapshot)
            sem_scope = seed(semantic, snapshot)
            for mode in ('plain', 'plain_followup', 'weak', 'weak_followup'):
                report['modes'][mode] = run_mode(rule, rule_scope, cases, mode)
            report['modes']['semantic_fixture'] = run_mode(
                semantic, sem_scope, cases, 'semantic_fixture')
            if args.model_dir:
                from pathlib import Path as _Path
                model_path = _Path(args.model_dir)
                if not model_path.is_dir() or not (model_path / 'config.json').exists():
                    parser.error('模型目录无效或缺少 config.json: %s' % args.model_dir)
                from astrbot_plugin_digital_memory_store.engine.e32.cpu_model import CPUSentenceEmbedder

                def factory(path):
                    return CPUSentenceEmbedder(path, cpu_threads=4, batch_size=8)

                real_dir = tempfile.mkdtemp()
                real = Store(real_dir, args.model_dir, factory)
                real_scope = seed(real, snapshot)
                report['modes']['semantic_real'] = run_mode(
                    real, real_scope, cases, 'semantic_real')
                report['model_dir'] = args.model_dir
        finally:
            rule.close()
            semantic.close()
            if real is not None:
                real.close()

    # 摘要：正例是否召回、负例是否被误召回、held-out 是否泄漏
    summary = {}
    for mode, rows in report['modes'].items():
        positives = [r for r in rows if not r['expected_empty']]
        negatives = [r for r in rows if r['expected_empty']]
        summary[mode] = {
            'recalled': sum(1 for r in positives if r['any_hit']),
            'positives': len(positives),
            'false_positive': sum(1 for r in negatives if r['called']),
            'negatives': len(negatives),
            'leaks': sum(1 for r in rows if r['any_leak']),
        }
    report['summary'] = summary

    out = ROOT / 'reports'
    out.mkdir(exist_ok=True)
    (out / 'baseline_retrieval.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print('mode'.ljust(16), 'recall'.ljust(8), 'false_pos'.ljust(10), 'leaks')
    for mode, s in summary.items():
        print(mode.ljust(16), f"{s['recalled']}/{s['positives']}".ljust(8),
              f"{s['false_positive']}/{s['negatives']}".ljust(10), s['leaks'])
    for mode in ('plain', 'semantic_fixture', 'semantic_real'):
        if mode not in report['modes']:
            continue
        print(f"\n逐案例（{mode}）：")
        for row in report['modes'][mode]:
            marks = ''.join('H' if t['hit'] else ('L' if t['leak'] else ('-' if not t['chars'] else '?'))
                            for t in row['turns'])
            print(f"  {row['case_id']:<20} turns={marks:<8} chars={[t['chars'] for t in row['turns']]}")
    print('\n说明：plain/weak 为规则与字面路径，0/2 是预期——上游线索刻意不提事件名。')
    print('     semantic_fixture 使用受控 4 维替身，只证明语义管线接通；不能作为准确率证据。')
    if 'semantic_real' in report['modes']:
        print('     semantic_real 使用真实 BGE，是准确率证据；请与上游实机结果逐案例对照。')


if __name__ == '__main__':
    main()
