"""真实 BGE 下的事件级相似度：事件向量到底能不能过 0.55？

事件向量不是标题，而是 `_snapshot_input`（标题 + 全部已审核原话）。
本脚本按插件真实建索引的方式计算，回答"事件召回是否可行"。
"""

import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import eval_baseline_retrieval as base  # noqa: E402
from astrbot_plugin_digital_memory_store.engine.e32.cpu_model import CPUSentenceEmbedder  # noqa: E402
from astrbot_plugin_digital_memory_store.engine.e419.dynamic_index import _snapshot_input  # noqa: E402
from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', required=True)
    args = parser.parse_args()

    cases, snapshot = base.load()
    report = {'threshold': 0.55, 'source_threshold': 0.75, 'cases': {}}
    with tempfile.TemporaryDirectory() as d:
        store = Store(d, args.model_dir,
                      lambda path: CPUSentenceEmbedder(path, cpu_threads=4, batch_size=8))
        try:
            scope = base.seed(store, snapshot)
            events = store.flow._events(scope)
            inputs = {event['id']: _snapshot_input(store.flow._snapshot(scope, event['id']))
                      for event in events}
            encoder = store._index().embedder
            vectors = {eid: encoder.encode_documents([text])[0] for eid, text in inputs.items()}
            titles = {event['id']: event['title'] for event in events}
            for case in cases:
                query = case['clues'][0]
                qv = encoder.encode_queries([query])[0]
                scores = {titles[eid]: round(cos(qv, vec), 4) for eid, vec in vectors.items()}
                expected = case.get('expected_snippet')
                target = None
                if expected:
                    for event in snapshot['events']:
                        if any(expected in source['content'] for source in event['sources']):
                            target = event['title']
                best_title, best_score = max(scores.items(), key=lambda item: item[1])
                row = {
                    'query': query,
                    'expected': expected,
                    'target_event': target,
                    'event_cosines': scores,
                    'best_event': best_title,
                    'best_score': best_score,
                    'passes_threshold': best_score >= 0.55,
                    'index_search': store._index().search(scope, query),
                }
                if target:
                    others = [value for title, value in scores.items() if title != target]
                    row['target_score'] = scores.get(target)
                    row['margin'] = round(scores[target] - max(others), 4) if others else None
                report['cases'][case['case_id']] = row
                print('\n==', case['case_id'], '|', query)
                print('   events:', scores, '| target:', target)
                print('   best=%s %.4f >=0.55? %s' % (best_title, best_score, row['passes_threshold']))
                print('   index.search:', row['index_search'])
        finally:
            store.close()

    out = ROOT / 'reports' / 'bge_events.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n已写入', out)


if __name__ == '__main__':
    main()
