"""M2 基线 runner：在 120 条分层语料上计算 R@1 / 误注入率 / 纠错正确率 / 延迟。

与 M1 探针的区别：这里走**自动路径**（capture → process_jobs → 向量/主题 → context_packet），
不是手工 save 事件；查询也不预先知道事件名。

用法：
    python evaluation/m2_runner.py                       # 无模型（字面/待审路径）
    python evaluation/m2_runner.py --model-dir <模型目录>  # 真实 BGE
    python evaluation/m2_runner.py --model-dir ... --out reports/m2_baseline_real.json
"""

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from m2_corpus import build_cases  # noqa: E402
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs  # noqa: E402
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402

BUDGET = 450


class _NoEncoder:
    model_key = 'm2-no-model'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        raise RuntimeError('semantic path disabled for this baseline')

    def encode_queries(self, texts):
        raise RuntimeError('semantic path disabled for this baseline')


def _factory(model_dir):
    if not model_dir:
        return _NoEncoder
    from astrbot_plugin_digital_memory_store.engine.e32.cpu_model import CPUSentenceEmbedder
    return lambda path: CPUSentenceEmbedder(path, cpu_threads=4, batch_size=8)


def configure(store, model_dir):
    """对齐插件 automatic_memory_enabled 的真实开关组合。"""
    store.model_dir = model_dir
    store.automatic_memory_enabled = True
    store.pending_context_enabled = True
    store.auto_group_context_enabled = True
    store.auto_gap_recovery_enabled = True
    store.auto_group_semantic_enabled = bool(model_dir)
    store.weak_association_enabled = False
    store.association_context_enabled = False
    store.semantic_source_context_enabled = False
    store.multi_event_context_enabled = False


def seed(store, scope, case, model_dir):
    for index, (text, stamp) in enumerate(case['messages']):
        store.capture(scope, '%s-%d' % (case['id'], index), text, recorded_at=stamp)
    process_jobs(store.flow.db)
    if model_dir:
        from astrbot_plugin_digital_memory_store.auto_cross_update import sync_text_index
        sync_text_index(store.flow.db, limit=100)
        store.sync_auto_source_vectors(limit=20)
        store.sync_auto_topics(limit=20)


def run_case(store, case, model_dir):
    scope = store.scope(case['id'], 'private')
    seed(store, scope, case, model_dir)
    as_of = datetime.fromisoformat(case['as_of'])
    started = time.perf_counter()
    if case.get('followup'):
        _first, focus = store.context_turn(
            scope, case['query'], None, budget=BUDGET,
            current_message_id='q1-%s' % case['id'], as_of=as_of)
        packet, _focus2 = store.context_turn(
            scope, case['followup'][0], focus, budget=BUDGET,
            current_message_id='q2-%s' % case['id'], as_of=as_of)
    else:
        packet = store.context_packet(
            scope, case['query'], budget=BUDGET,
            current_message_id='q-%s' % case['id'], as_of=as_of)
    elapsed_ms = (time.perf_counter() - started) * 1000

    expect = case.get('expect') or []
    forbid = case.get('forbid') or []
    row = {
        'id': case['id'], 'category': case['category'], 'kind': case['kind'],
        'query': case['query'], 'packet_chars': len(packet),
        'latency_ms': round(elapsed_ms, 2),
        'r1': bool(expect) and expect[0] in packet,
        'missing': [text for text in expect if text not in packet],
        'forbidden_hit': [text for text in forbid if text and text in packet],
        'unconfirmed_marked': '未确认' in packet,
        'background_only': packet.startswith('{"背景上下文"'),
    }
    if case['kind'] == 'correction':
        row['correction_ok'] = bool(expect) and all(text in packet for text in expect) \
            and row['unconfirmed_marked']
    if case['kind'] == 'negative':
        # 背景包是“每轮供给”的兜底，不是把无关内容当相关证据；单列统计。
        row['false_injected'] = bool(packet.strip()) and not row['background_only']
    if not row['r1'] and case['kind'] != 'negative':
        row['packet_head'] = packet[:160]
    return row


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def summarize(rows):
    positives = [row for row in rows if row['kind'] != 'negative']
    negatives = [row for row in rows if row['kind'] == 'negative']
    corrections = [row for row in rows if row['kind'] == 'correction']
    categories = {}
    for row in rows:
        entry = categories.setdefault(row['category'], {'n': 0, 'r1': 0, 'correction': 0,
                                                        'correction_n': 0, 'false': 0,
                                                        'false_n': 0})
        entry['n'] += 1
        if row['kind'] == 'negative':
            entry['false_n'] += 1
            entry['false'] += 1 if row['false_injected'] else 0
        else:
            entry['r1'] += 1 if row['r1'] else 0
            if 'correction_ok' in row:
                entry['correction_n'] += 1
                entry['correction'] += 1 if row['correction_ok'] else 0
    latencies = [row['latency_ms'] for row in rows]
    return {
        'total': len(rows), 'positives': len(positives), 'negatives': len(negatives),
        'r1': sum(1 for row in positives if row['r1']),
        'false_injected': sum(1 for row in negatives if row['false_injected']),
        'corrections': len(corrections),
        'correction_ok': sum(1 for row in corrections if row['correction_ok']),
        'forbidden_hits': sum(1 for row in rows if row['forbidden_hit']),
        'supplied': sum(1 for row in rows if row['packet_chars'] > 0),
        'background_only': sum(1 for row in rows if row.get('background_only')),
        'latency_ms': {'p50': percentile(latencies, 0.5), 'p95': percentile(latencies, 0.95),
                       'max': max(latencies) if latencies else None},
        'categories': categories,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', default='')
    parser.add_argument('--out', default='')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--source-threshold', type=float, default=None,
                        help='原话级相似度门槛实验值（默认沿用 Store 的 0.75）')
    parser.add_argument('--always-context', action='store_true',
                        help='按产品默认开启“每轮背景上下文”后再评分')
    args = parser.parse_args()

    cases = build_cases()
    if args.limit:
        cases = cases[:args.limit]

    with tempfile.TemporaryDirectory(prefix='m2-eval-') as d:
        store = Store(d, args.model_dir, _factory(args.model_dir))
        configure(store, args.model_dir)
        if args.source_threshold is not None:
            store.source_min_cosine = args.source_threshold
        if args.always_context:
            store.always_context_enabled = True
        try:
            rows = [run_case(store, case, args.model_dir) for case in cases]
        finally:
            store.close()

    summary = summarize(rows)
    mode = 'real-bge' if args.model_dir else 'no-model'
    report = {'suite': 'm2-stratified-v1', 'mode': mode, 'model_dir': args.model_dir,
              'corpus': {'total': len(cases), 'source': 'built-in stratified corpus (authored)'},
              'summary': summary, 'rows': rows}
    out = Path(args.out) if args.out else (
        ROOT / 'reports' / ('m2_baseline_real_bge.json' if args.model_dir
                            else 'm2_baseline_no_model.json'))
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print('模式:', mode)
    print('正例 R@1: %d/%d (%.1f%%)' % (summary['r1'], summary['positives'],
                                        100.0 * summary['r1'] / max(1, summary['positives'])))
    print('负例误注入: %d/%d' % (summary['false_injected'], summary['negatives']))
    print('纠错同时给新旧+未确认: %d/%d' % (summary['correction_ok'], summary['corrections']))
    print('禁注命中（相似事件串味）: %d 条' % summary['forbidden_hits'])
    print('延迟 P50/P95: %.1f / %.1f ms' % (summary['latency_ms']['p50'],
                                            summary['latency_ms']['p95']))
    print('分类:')
    for name, entry in summary['categories'].items():
        extra = ''
        if entry['correction_n']:
            extra += ' 纠错 %d/%d' % (entry['correction'], entry['correction_n'])
        if entry['false_n']:
            extra += ' 误注入 %d/%d' % (entry['false'], entry['false_n'])
        print('  %-14s R@1 %d/%d%s' % (name, entry['r1'],
                                       entry['n'] - entry['false_n'], extra))
    print('已写入', out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
