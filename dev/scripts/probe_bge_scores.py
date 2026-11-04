"""用真实 BGE 打印 4 个上游案例的相似度分数，解释 semantic_real 为什么 0/2。

不修改任何阈值，只输出事实：
  - query 与 3 条旧来源原文的余弦
  - query 与事件标题的余弦
  - 动态索引 search 的原始返回
  - context_packet 实际给出的内容长度与前 200 字
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
    sources = [(src['external_source_id'], src['content'])
               for event in snapshot['events'] for src in event['sources']]
    titles = [event['title'] for event in snapshot['events']]
    report = {}

    with tempfile.TemporaryDirectory() as d:
        store = Store(d, args.model_dir,
                      lambda path: CPUSentenceEmbedder(path, cpu_threads=4, batch_size=8))
        try:
            scope = base.seed(store, snapshot)
            encoder = store._index().embedder
            doc_vectors = encoder.encode_documents([content for _, content in sources])
            title_vectors = encoder.encode_documents(titles)
            for case in cases:
                query = case['clues'][0]
                qv = encoder.encode_queries([query])[0]
                row = {
                    'case_id': case['case_id'],
                    'query': query,
                    'expected': case.get('expected_snippet'),
                    'source_cosines': {sid: round(cos(qv, vec), 4)
                                       for (sid, _), vec in zip(sources, doc_vectors)},
                    'title_cosines': {title: round(cos(qv, vec), 4)
                                      for title, vec in zip(titles, title_vectors)},
                    'index_search': store._index().search(scope, query),
                }
                if case.get('expected_snippet'):
                    target = next((vec for (sid, content), vec in zip(sources, doc_vectors)
                                   if case['expected_snippet'] in content), None)
                    if target is not None:
                        row['expected_source_cosine'] = round(cos(qv, target), 4)
                packet = store.context_packet(scope, query, budget=800, as_of=base.AS_OF)
                row['packet_chars'] = len(packet)
                row['packet_head'] = packet[:200]
                report[case['case_id']] = row
                print('\n==', case['case_id'], '| query:', query)
                print('   expected:', case.get('expected_snippet'))
                print('   source cos:', row['source_cosines'])
                print('   title  cos:', row['title_cosines'])
                if 'expected_source_cosine' in row:
                    print('   expected source cos:', row['expected_source_cosine'])
                print('   index.search:', row['index_search'])
                print('   packet:', row['packet_chars'], 'chars |', row['packet_head'][:160])
        finally:
            store.close()

    out = Path(__file__).resolve().parents[1] / 'reports' / 'bge_scores.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n已写入', out)


if __name__ == '__main__':
    main()
