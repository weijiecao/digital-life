"""L0 soak 记录器的离线测试：只验证采集/增量/汇总逻辑，不启动 AstrBot。"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from test_store import FakeEncoder

DEV_ROOT = Path(__file__).resolve().parent
TESTBED = DEV_ROOT.parent / 'testbed'


def load_soak_module():
    spec = importlib.util.spec_from_file_location(
        'dlm_soak', TESTBED / 'harness' / 'soak.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSoakCollect(unittest.TestCase):
    def setUp(self):
        self.module = load_soak_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'manual_test_memory.sqlite3'
        self.serve_log = self.root / 'serve.log'
        self.mock_log = self.root / 'llm_requests.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_db(self):
        from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
        from astrbot_plugin_digital_memory_store.storage import Store
        store = Store(self.root, '', FakeEncoder)
        try:
            scope = store.scope('1', 's')
            store.capture(scope, 'm1', '计划去青岛海边散步',
                          recorded_at='2025-01-01T12:00:00+00:00')
            store.capture(scope, 'm2', '周末在家整理书架',
                          recorded_at='2025-01-02T12:00:00+00:00')
            process_jobs(store.flow.db)
            store.record_capture_gap(scope, '2025-01-03T12:00:00+00:00')
        finally:
            store.close()

    def test_collects_db_logs_and_provider_counts(self):
        self._seed_db()
        self.serve_log.write_text('INFO ok\nERROR boom\nTraceback ...\n', encoding='utf-8')
        self.mock_log.write_text(
            json.dumps({'payload': {'messages': [{'content': '普通'}]}}, ensure_ascii=False) + '\n'
            + json.dumps({'payload': {'messages': [{'content': '[DL_MEMORY_V040] x'}]}},
                         ensure_ascii=False) + '\n', encoding='utf-8')
        metrics = self.module.collect(paths={'db': self.db, 'serve_log': self.serve_log,
                                             'mock_log': self.mock_log})
        self.assertTrue(metrics['db']['available'])
        self.assertEqual(metrics['db']['captures'], 2)
        self.assertEqual(metrics['db']['gaps'], 1)
        self.assertEqual(metrics['db']['jobs'].get('done'), 2)
        self.assertEqual(metrics['logs']['serve_new_errors'], 2)
        self.assertEqual(metrics['logs']['provider_new_requests'], 2)
        self.assertEqual(metrics['logs']['provider_new_injected'], 1)

    def test_offsets_only_count_new_lines(self):
        self._seed_db()
        self.serve_log.write_text('INFO ok\n', encoding='utf-8')
        self.mock_log.write_text('{"payload":{}}\n', encoding='utf-8')
        first = self.module.collect(paths={'db': self.db, 'serve_log': self.serve_log,
                                           'mock_log': self.mock_log})
        offsets = first['offsets']
        with self.serve_log.open('a', encoding='utf-8') as handle:
            handle.write('ERROR later\n')
        with self.mock_log.open('a', encoding='utf-8') as handle:
            handle.write('{"payload":{"messages":[{"content":"[DL_MEMORY_V040] y"}]}}\n')
        second = self.module.collect(paths={'db': self.db, 'serve_log': self.serve_log,
                                            'mock_log': self.mock_log}, offsets=offsets)
        self.assertEqual(second['logs']['serve_new_errors'], 1)
        self.assertEqual(second['logs']['provider_new_requests'], 1)
        self.assertEqual(second['logs']['provider_new_injected'], 1)

    def test_latency_metrics_are_collected_and_summarized(self):
        self._seed_db()
        latency = self.root / 'latency.jsonl'
        latency.write_text('{"ms": 10, "injected": false}\n{"ms": 30, "injected": true}\n',
                           encoding='utf-8')
        metrics = self.module.collect(paths={'db': self.db, 'serve_log': self.serve_log,
                                             'mock_log': self.mock_log, 'latency': latency})
        self.assertEqual(metrics['latency']['turns'], 2)
        self.assertEqual(metrics['latency']['injected'], 1)
        self.assertEqual(metrics['latency']['p95'], 30)
        summary = self.module.summarize([metrics])
        self.assertEqual(summary['hook_turns'], 2)
        self.assertEqual(summary['hook_injected'], 1)
        self.assertEqual(summary['hook_injection_rate'], 0.5)

    def test_latency_offsets_only_count_new_turns(self):
        self._seed_db()
        latency = self.root / 'latency.jsonl'
        latency.write_text('{"ms": 5, "injected": false}\n', encoding='utf-8')
        first = self.module.collect(paths={'db': self.db, 'serve_log': self.serve_log,
                                           'mock_log': self.mock_log, 'latency': latency})
        with latency.open('a', encoding='utf-8') as handle:
            handle.write('{"ms": 7, "injected": true}\n')
        second = self.module.collect(paths={'db': self.db, 'serve_log': self.serve_log,
                                            'mock_log': self.mock_log, 'latency': latency},
                                     offsets=first['offsets'])
        self.assertEqual(second['latency']['turns'], 1)
        self.assertEqual(second['latency']['injected'], 1)

    def test_missing_database_is_safe(self):
        metrics = self.module.collect(paths={'db': self.root / 'nope.sqlite3',
                                             'serve_log': self.serve_log,
                                             'mock_log': self.mock_log})
        self.assertFalse(metrics['db']['available'])
        summary = self.module.summarize([metrics])
        self.assertEqual(summary['ticks'], 1)
        self.assertEqual(summary['captures_delta'], 0)


def load_soak_report_module():
    spec = importlib.util.spec_from_file_location(
        'dlm_soak_report', TESTBED / 'harness' / 'soak_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSoakReport(unittest.TestCase):
    def setUp(self):
        self.module = load_soak_report_module()
        self.rows = [
            {'ts': '2026-11-04T00:00:00', 'db': {'captures': 10, 'gaps': 0},
             'latency': {'turns': 10, 'injected': 9, 'p95': 120},
             'logs': {'serve_new_errors': 0},
             'serve': {'pid': 1, 'rss_mb': 500}, 'ports': {'reverse_ws': True}},
            {'ts': '2026-11-05T00:00:00', 'db': {'captures': 20, 'gaps': 0},
             'latency': {'turns': 10, 'injected': 10, 'p95': 150},
             'logs': {'serve_new_errors': 0},
             'serve': {'pid': 1, 'rss_mb': 510}, 'ports': {'reverse_ws': True}},
        ]

    def test_aggregate_totals(self):
        agg = self.module.aggregate(self.rows)
        self.assertEqual(agg['ticks'], 2)
        self.assertEqual(agg['captures_delta'], 10)
        self.assertEqual(agg['hook_turns'], 20)
        self.assertEqual(agg['hook_injected'], 19)
        self.assertEqual(agg['hook_p95_max'], 150)
        self.assertEqual(agg['restarts'], 0)
        self.assertEqual(agg['connected_ratio'], 1.0)
        self.assertEqual(agg['hours'], 24.0)

    def test_judge_fails_on_capture_rate_and_passes_on_full(self):
        agg = self.module.aggregate(self.rows)
        bad = self.module.judge(agg, {'d1': 20}, require_hours=1)
        self.assertEqual(bad['checks']['采集成功率']['value'], 0.5)
        self.assertEqual(bad['blocking'], 1)
        good = self.module.judge(agg, {'d1': 10}, require_hours=1)
        self.assertEqual(good['blocking'], 0)
        self.assertTrue(good['ok'])

    def test_judge_flags_short_coverage(self):
        agg = self.module.aggregate(self.rows)
        verdict = self.module.judge(agg, {'d1': 10}, require_hours=72)
        self.assertFalse(verdict['ok'])
        self.assertFalse(verdict['checks']['覆盖时长']['pass'])

    def test_render_contains_conclusion(self):
        agg = self.module.aggregate(self.rows)
        verdict = self.module.judge(agg, {'d1': 10}, require_hours=1)
        text = self.module.render(agg, verdict)
        self.assertIn('L0 soak 汇总', text)
        self.assertIn('结论', text)


class TestSoakSummary(unittest.TestCase):
    def setUp(self):
        self.module = load_soak_module()

    def test_restart_and_injection_rate(self):
        def row(pid, captures, requests, injected, errors=0):
            return {'ts': 't', 'db': {'captures': captures, 'gaps': 0, 'jobs': {}},
                    'serve': {'pid': pid, 'rss_mb': 100}, 'ports': {'reverse_ws': True},
                    'logs': {'serve_new_errors': errors,
                             'provider_new_requests': requests,
                             'provider_new_injected': injected}}
        rows = [row(1, 10, 2, 1, errors=1), row(1, 12, 3, 2), row(2, 13, 1, 1)]
        summary = self.module.summarize(rows)
        self.assertEqual(summary['ticks'], 3)
        self.assertEqual(summary['captures_delta'], 3)
        self.assertEqual(summary['restarts'], 1)
        self.assertEqual(summary['provider_requests'], 6)
        self.assertEqual(summary['provider_injected'], 4)
        self.assertAlmostEqual(summary['injection_rate'], round(4 / 6, 3))
        self.assertEqual(summary['serve_error_lines'], 1)
        self.assertEqual(summary['rss_mb_max'], 100)

    def test_empty_rows(self):
        self.assertEqual(self.module.summarize([]), {'ticks': 0})


if __name__ == '__main__':
    unittest.main()
