"""真实语料评分：用真实聊天导入的库 + 人工标注的探针，计算 R@1 / 误注入 / 纠错 / 延迟。

探针格式（JSON）：
{
  "probes": [
    {"session": "s1", "query": "喜欢吃苹果吗", "expect": ["我喜欢吃苹果"],
     "require_unconfirmed": true},
    {"session": "s1", "query": "火星飞船怎么租？", "forbid": ["公园跑步"]}
  ]
}

- 有 expect：全部命中才算 hit；require_unconfirmed 时包内必须有“未确认”。
- 只有 forbid（负例）：包内不得出现任何 forbid 片段；没有预期内容。
- session 必须与导入时的 id 一致。

用法：
    python evaluation/real_corpus_eval.py --db-dir reports/real_corpus_db \
        --probes probes.json --model-dir ../testbed/models/bge-small-zh-v1.5
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from m2_runner import _factory, configure  # noqa: E402
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402


def load_probes(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    probes = payload.get('probes', payload)
    if not isinstance(probes, list) or not probes:
        raise ValueError('没有探针')
    return probes


def percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return None
    position = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return round(ordered[position], 2)


def evaluate(probes, db_dir, model_dir='', budget=450):
    store = Store(str(db_dir), model_dir, _factory(model_dir))
    configure(store, model_dir)
    store.automatic_memory_enabled = False
    model_load_ms = None
    if model_dir:
        started = time.perf_counter()
        try:
            store._get_embedder()
            model_load_ms = round((time.perf_counter() - started) * 1000, 1)
        except Exception:
            model_load_ms = None
    rows = []
    try:
        for index, probe in enumerate(probes):
            session = str(probe['session'])
            scope = store.scope(session, 'real')
            query = str(probe['query'])
            started = time.perf_counter()
            packet = store.context_packet(scope, query, budget=budget,
                                          current_message_id='probe-%d' % index)
            elapsed = (time.perf_counter() - started) * 1000
            expect = [str(item) for item in (probe.get('expect') or [])]
            forbid = [str(item) for item in (probe.get('forbid') or [])]
            hit = all(item in packet for item in expect) if expect else None
            if probe.get('require_unconfirmed'):
                hit = bool(hit) and ('未确认' in packet)
            forbidden = [item for item in forbid if item in packet]
            rows.append({'session': session, 'query': query,
                         'expect': expect, 'hit': hit,
                         'forbid_hit': forbidden,
                         'packet_chars': len(packet),
                         'latency_ms': round(elapsed, 2)})
    finally:
        store.close()
    positives = [row for row in rows if row['expect']]
    negatives = [row for row in rows if not row['expect']]
    latencies = [row['latency_ms'] for row in rows]
    summary = {
        'probes': len(rows),
        'positives': len(positives),
        'hits': sum(1 for row in positives if row['hit']),
        'negatives': len(negatives),
        'negative_ok': sum(1 for row in negatives if not row['forbid_hit']),
        'forbid_hits': sum(1 for row in rows if row['forbid_hit']),
        'model_load_ms': model_load_ms,
        'latency_ms': {'p50': percentile(latencies, 50),
                       'p95': percentile(latencies, 95),
                       'max': round(max(latencies), 2) if latencies else None},
    }
    return {'suite': 'real-corpus-v1', 'summary': summary, 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description='真实语料探针评分')
    parser.add_argument('--db-dir', default=str(ROOT / 'reports' / 'real_corpus_db'))
    parser.add_argument('--probes', required=True)
    parser.add_argument('--model-dir', default='')
    parser.add_argument('--out', default=str(ROOT / 'reports' / 'real_corpus.json'))
    args = parser.parse_args()
    report = evaluate(load_probes(args.probes), args.db_dir, args.model_dir)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    print('已写入', args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
