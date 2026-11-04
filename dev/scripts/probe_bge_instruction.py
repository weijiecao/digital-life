"""对比 BGE 查询指令对相似度的影响（不改插件，只测量）。

背景：bge-*-zh 模型卡建议检索时给 query 加指令
  "为这个句子生成表示以用于检索相关文章："
而插件 CPUSentenceEmbedder 默认 query_instruction=''。

输出每个案例：目标来源与全部来源/标题的余弦，以及"目标 - 最高负例"的差值。
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

INSTRUCTION = '为这个句子生成表示以用于检索相关文章：'


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

    plain = CPUSentenceEmbedder(args.model_dir, cpu_threads=4, batch_size=8)
    instructed = CPUSentenceEmbedder(args.model_dir, cpu_threads=4, batch_size=8,
                                     query_instruction=INSTRUCTION)
    doc_vectors = plain.encode_documents([content for _, content in sources])
    title_vectors = plain.encode_documents(titles)

    report = {'model_dir': args.model_dir, 'instruction': INSTRUCTION, 'cases': {}}
    for case in cases:
        entry = {'query': case['clues'][0], 'expected': case.get('expected_snippet')}
        for label, encoder in (('no_instruction', plain), ('with_instruction', instructed)):
            qv = encoder.encode_queries([case['clues'][0]])[0]
            source_scores = {sid: round(cos(qv, vec), 4)
                             for (sid, _), vec in zip(sources, doc_vectors)}
            title_scores = {title: round(cos(qv, vec), 4)
                            for title, vec in zip(titles, title_vectors)}
            target = None
            if case.get('expected_snippet'):
                for (sid, content), vec in zip(sources, doc_vectors):
                    if case['expected_snippet'] in content:
                        target = sid
                        break
            entry[label] = {
                'source_cosines': source_scores,
                'title_cosines': title_scores,
                'target_source': target,
                'target_cosine': source_scores.get(target) if target else None,
                'max_other_source': max((v for k, v in source_scores.items() if k != target),
                                        default=None),
            }
            if target:
                entry[label]['margin'] = round(
                    source_scores[target] - max(v for k, v in source_scores.items() if k != target), 4)
        report['cases'][case['case_id']] = entry

        print('\n==', case['case_id'], '|', case['clues'][0])
        for label in ('no_instruction', 'with_instruction'):
            row = entry[label]
            print('   %-16s target=%s cos=%s max_other=%s margin=%s'
                  % (label, row['target_source'], row['target_cosine'],
                     row['max_other_source'], row.get('margin')))
            print('      titles:', row['title_cosines'])

    out = ROOT / 'reports' / 'bge_instruction.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n已写入', out)


if __name__ == '__main__':
    main()
