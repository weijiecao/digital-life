"""纠错成组回归：相邻变化短句即使带主题前缀（“搬家改到…”）也要与旧原话一起给出。"""

import tempfile
import unittest

from .test_store import FakeEncoder

from astrbot_plugin_digital_memory_store.auto_organize import process_jobs
from astrbot_plugin_digital_memory_store.storage import Store


class TestCorrectionChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, '', FakeEncoder)
        self.store.automatic_memory_enabled = True
        self.store.pending_context_enabled = True
        self.scope = self.store.scope('1', 's')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _row(self, seq):
        return self.store.flow.db.execute(
            'SELECT seq,content,content_sha,recorded_at,state FROM dl_capture WHERE seq=?',
            (seq,)).fetchone()

    def test_pending_chain_accepts_topic_prefixed_update(self):
        self.store.capture(self.scope, 'a', '下个月十五号搬到杭州西湖区',
                           recorded_at='2025-01-01T10:00:00+00:00')
        self.store.capture(self.scope, 'b', '搬家改到滨江区',
                           recorded_at='2025-01-01T10:02:00+00:00')
        process_jobs(self.store.flow.db)
        packet = self.store.context_packet(self.scope, '之前搬到杭州西湖区那件事怎么样',
                                           budget=450, current_message_id='q')
        self.assertIn('下个月十五号搬到杭州西湖区', packet)
        self.assertIn('搬家改到滨江区', packet, '带主题前缀的变化短句必须进链')
        self.assertIn('未确认', packet)

    def test_peer_helper_returns_adjacent_update_only(self):
        self.store.capture(self.scope, 'a', '科目二练车定在周六早上',
                           recorded_at='2025-01-01T10:00:00+00:00')
        self.store.capture(self.scope, 'b', '练车挪到周一早上',
                           recorded_at='2025-01-01T10:02:00+00:00')
        peer = self.store._possible_update_peer(self.scope, self._row(1))
        self.assertIsNotNone(peer)
        self.assertEqual(peer['seq'], 2)

        self.store.capture(self.scope, 'c', '周末在家整理书架',
                           recorded_at='2025-01-01T10:03:00+00:00')
        self.assertIsNone(self.store._possible_update_peer(self.scope, self._row(2)),
                          '无变化词的下一条不算更新')

        self.store.capture(self.scope, 'd', '体检改到周五上午',
                           recorded_at='2025-01-01T13:00:00+00:00')
        self.assertIsNone(self.store._possible_update_peer(self.scope, self._row(3)),
                          '超过 180 秒不算相邻更新')


class TestCorrectionCancelCue(unittest.TestCase):
    def test_cancel_phrase_is_recognised(self):
        from astrbot_plugin_digital_memory_store.storage import _UPDATE_CUES
        for text in ('疫苗先不打了', '书店这周不去了', '算了不弄了'):
            self.assertTrue(_UPDATE_CUES.search(text), text)


if __name__ == '__main__':
    unittest.main()
