"""E5.0-F 基线对齐：把上游 pilot 的 8 条冻结话术接入下游 Store，验证采集层与阈值继承。

上游：DigitalLife_Memory_E50F_Standalone 的 e50f.pilot（实机基线）
下游：astrbot_plugin_digital_memory_store v0.16.0

本测试只验证"结构继承"：同样的冻结语料、同样的单会话分段、同样的 0.55、同样的采集上限。
真 BGE 的语义对齐需在拿到模型后进行，见 tests/README。
"""
import hashlib
import inspect
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot_plugin_digital_memory_store.storage import Store, MAX_CAPTURE_CHARS
from astrbot_plugin_digital_memory_store.engine.e419.dynamic_index import attach_dynamic_index

FIXTURES = Path(__file__).with_name('fixtures')
CASES = FIXTURES / 'cases_fixture.json'
SNAPSHOT = FIXTURES / 'reviewed_fixture.json'
BASE = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)


class _Encoder:
    model_key = 'baseline-alignment-fixture'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def encode_queries(self, texts):
        return [[1.0, 0.0] for _ in texts]


def load_fixtures():
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    snapshot = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    messages = [clue for case in cases['cases'] for clue in case['clues']]
    return cases, snapshot, messages


class TestBaselineAlignment(unittest.TestCase):
    def test_frozen_fixture_structure_and_hashes(self):
        cases, snapshot, messages = load_fixtures()
        self.assertEqual(cases['format'], 'digitallife-e50b-cases-v1')
        self.assertEqual(snapshot['format'], 'digitallife-reviewed-snapshot-v1')
        self.assertEqual(len(cases['cases']), 4)
        self.assertEqual([len(c['clues']) for c in cases['cases']], [3, 2, 2, 1])
        self.assertEqual(len(messages), 8)
        # 冻结来源的内容哈希必须与夹具声明一致，否则上游基线不可比。
        for event in snapshot['events']:
            for source in event['sources']:
                digest = hashlib.sha256(source['content'].encode('utf-8')).hexdigest()
                self.assertEqual(digest, source['sha256'], event['external_id'])
                self.assertEqual(source['review_state'], 'approved_link')

    def test_held_out_details_never_appear_in_new_clues(self):
        cases, _, messages = load_fixtures()
        joined = '\n'.join(messages)
        for case in cases['cases']:
            detail = case.get('old_only_detail')
            if detail:
                self.assertNotIn(detail, joined, case['case_id'])

    def test_old_fixture_predates_new_messages(self):
        _, snapshot, _ = load_fixtures()
        for event in snapshot['events']:
            for source in event['sources']:
                recorded = datetime.fromisoformat(source['recorded_at'])
                self.assertLess(recorded, BASE)

    def test_capture_accepts_baseline_script_in_one_scope(self):
        _, _, messages = load_fixtures()
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, '', _Encoder)
            try:
                scope = store.scope('baseline-user', 'private')
                for i, text in enumerate(messages, 1):
                    stamp = (BASE + timedelta(minutes=i)).isoformat(timespec='seconds')
                    self.assertTrue(store.capture(scope, 'baseline-%d' % i, text, recorded_at=stamp))
                rows = store.flow.db.execute(
                    'SELECT seq,content,recorded_at,state FROM dl_capture WHERE scope=? ORDER BY seq',
                    (scope,)).fetchall()
                self.assertEqual(len(rows), 8)
                self.assertEqual([r['seq'] for r in rows], list(range(1, 9)))
                self.assertEqual([r['content'] for r in rows], messages)
                self.assertEqual({r['state'] for r in rows}, {'pending'})
                self.assertEqual(store.capture_gap_count(scope), 0)
                # 同事务任务已入库，来源变化后仍可继续整理。
                jobs = store.flow.db.execute(
                    'SELECT COUNT(*) FROM dl_auto_jobs WHERE scope=?', (scope,)).fetchone()[0]
                self.assertEqual(jobs, 8)
            finally:
                store.close()

    def test_capture_is_idempotent_but_rejects_changed_content(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, '', _Encoder)
            try:
                scope = store.scope('baseline-user', 'private')
                self.assertTrue(store.capture(scope, 'same-id', '书店', recorded_at=BASE.isoformat()))
                self.assertFalse(store.capture(scope, 'same-id', '书店', recorded_at=BASE.isoformat()))
                with self.assertRaises(ValueError):
                    store.capture(scope, 'same-id', '书店（改）', recorded_at=BASE.isoformat())
            finally:
                store.close()

    def test_inherited_thresholds_and_limits(self):
        # 上游基线固定 0.55；下游索引层必须保持同一默认值，否则对照无效。
        default = inspect.signature(attach_dynamic_index).parameters['min_cosine'].default
        self.assertEqual(default, 0.55)
        self.assertEqual(MAX_CAPTURE_CHARS, 4000)


if __name__ == '__main__':
    unittest.main()
