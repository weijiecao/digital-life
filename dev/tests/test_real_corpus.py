"""真实语料导入 + 探针评分管线的回归（无模型，字面/待审路径）。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEV_ROOT / 'evaluation'))

from real_corpus_eval import evaluate, load_probes  # noqa: E402
from real_corpus_import import import_corpus, load_sessions  # noqa: E402

SESSIONS = [
    {'id': 's1', 'messages': [
        {'text': '我喜欢吃苹果', 'at': '2025-01-01T10:00:00+08:00'},
        {'text': '周末去公园跑步', 'at': '2025-01-01T10:01:00+08:00'},
        {'text': '', 'at': '2025-01-01T10:02:00+08:00'},
    ]},
    {'id': 's2', 'messages': [
        {'text': '下个月十五号搬到杭州西湖区', 'at': '2025-01-02T10:00:00+08:00'},
        {'text': '搬家改到滨江区', 'at': '2025-01-02T10:02:00+08:00'},
    ]},
]


class TestImport(unittest.TestCase):
    def test_import_counts_and_skips(self):
        with tempfile.TemporaryDirectory() as db:
            stats = import_corpus(SESSIONS, db)
            self.assertEqual(stats['sessions'], 2)
            self.assertEqual(stats['imported'], 4)
            self.assertEqual(stats['skipped'], 1, '空消息应跳过')
            self.assertTrue((Path(db) / 'manual_test_memory.sqlite3').is_file())

    def test_load_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'corpus.jsonl'
            path.write_text('\n'.join(json.dumps(item, ensure_ascii=False)
                                      for item in SESSIONS), encoding='utf-8')
            self.assertEqual(len(load_sessions(path)), 2)

    def test_empty_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'empty.json'
            path.write_text('{"sessions": []}', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_sessions(path)


class TestEvaluate(unittest.TestCase):
    def _run(self, probes):
        with tempfile.TemporaryDirectory() as db:
            import_corpus(SESSIONS, db)
            return evaluate(probes, db)

    def test_hits_correction_and_negative(self):
        report = self._run([
            {'session': 's1', 'query': '喜欢吃苹果吗',
             'expect': ['我喜欢吃苹果'], 'require_unconfirmed': True},
            {'session': 's1', 'query': '周末去公园跑步了吗',
             'expect': ['周末去公园跑步'], 'require_unconfirmed': True},
            {'session': 's1', 'query': '火星飞船怎么租？',
             'forbid': ['公园跑步', '苹果']},
            {'session': 's2', 'query': '之前搬到杭州西湖区那件事怎么样',
             'expect': ['下个月十五号搬到杭州西湖区', '搬家改到滨江区'],
             'require_unconfirmed': True},
        ])
        summary = report['summary']
        self.assertEqual(summary['positives'], 3)
        self.assertEqual(summary['hits'], 3, report['rows'])
        self.assertEqual(summary['negatives'], 1)
        self.assertEqual(summary['negative_ok'], 1)
        self.assertEqual(summary['forbid_hits'], 0)
        self.assertIsNotNone(summary['latency_ms']['p50'])

    def test_load_probes_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'probes.json'
            path.write_text(json.dumps({'probes': [
                {'session': 's1', 'query': 'q', 'expect': ['x']}]}, ensure_ascii=False),
                encoding='utf-8')
            self.assertEqual(len(load_probes(path)), 1)


if __name__ == '__main__':
    unittest.main()
