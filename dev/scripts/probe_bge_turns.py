"""真实 BGE 的逐轮分数：哪一轮有语义信号、0.55/0.75 门槛是否合理。

对每个案例的每条线索，输出：
  - 与各事件向量的余弦（事件召回门槛 0.55）
  - 目标事件内的原话余弦、最高其它原话、差值（原话门槛 0.75 / 领先 0.10）
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
    report = {'event_threshold': 0.55, 'source_threshold': 0.75, 'lead': 0.10, 'cases': {}}
    with tempfile.TemporaryDirectory() as d:
        store = Store(d, args.model_dir,
                      lambda path: CPUSentenceEmbedder(path, cpu_threads=4, batch_size=8))
        try:
            scope = base.seed(store, snapshot)
            events = store.flow._events(scope)
            inputs = {event['id']: _snapshot_input(store.flow._snapshot(scope, event['id']))
                      for event in events}
            encoder = store._index().embedder
            event_vectors = {eid: encoder.encode_documents([text])[0]
                             for eid, text in inputs.items()}
            titles = {event['id']: event['title'] for event in events}

            for case in cases:
                expected = case.get('expected_snippet')
                target_event = None
                if expected:
                    for event in snapshot['events']:
                        if any(expected in source['content'] for source in event['sources']):
                            target_event = event['title']
                entry = {'expected': expected, 'target_event': target_event, 'turns': []}
                for index, clue in enumerate(case['clues'], 1):
                    qv = encoder.encode_queries([clue])[0]
                    event_scores = {titles[eid]: round(cos(qv, vec), 4)
                                    for eid, vec in event_vectors.items()}
                    best_event, best_score = max(event_scores.items(), key=lambda item: item[1])
                    turn = {'turn': index, 'clue': clue,
                            'event_cosines': event_scores,
                            'best_event': best_event, 'best_event_score': best_score,
                            'event_pass': best_score >= 0.55,
                            'correct_event': best_event == target_event}
                    if target_event:
                        snap = store.flow._snapshot(scope, next(
                            e['id'] for e in events if e['title'] == target_event))
                        rows = snap['rows']
                        source_vectors = encoder.encode_documents([row['content'] for row in rows])
                        source_scores = [round(cos(qv, vec), 4) for vec in source_vectors]
                        target_rank = [i for i, row in enumerate(rows) if expected and
                                       expected in row['content']]
                        target_score = source_scores[target_rank[0]] if target_rank else None
                        others = [s for i, s in enumerate(source_scores) if i not in target_rank]
                        turn.update({
                            'source_scores': source_scores,
                            'target_source_score': target_score,
                            'max_other_source': max(others) if others else None,
                            'lead': (round(target_score - max(others), 4)
                                     if target_score is not None and others else None),
                            'source_pass_075': bool(target_score is not None and target_score >= 0.75
                                                    and (not others or target_score - max(others) >= 0.10)),
                            'source_pass_065': bool(target_score is not None and target_score >= 0.65
                                                    and (not others or target_score - max(others) >= 0.10)),
                        })
                    entry['turns'].append(turn)
                report['cases'][case['case_id']] = entry
                print('\n==', case['case_id'], '| target event:', target_event)
                for turn in entry['turns']:
                    print('  T%d %-28s best=%s %.3f pass55=%s correct=%s'
                          % (turn['turn'], turn['clue'][:24], turn['best_event'],
                             turn['best_event_score'], turn['event_pass'],
                             turn.get('correct_event')))
                    if 'target_source_score' in turn:
                        print('     src target=%s other=%s lead=%s  pass.75=%s pass.65=%s'
                              % (turn['target_source_score'], turn['max_other_source'],
                                 turn['lead'], turn['source_pass_075'], turn['source_pass_065']))
        finally:
            store.close()

    out = ROOT / 'reports' / 'bge_turns.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n已写入', out)


if __name__ == '__main__':
    main()
