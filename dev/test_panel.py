"""控制面板后端：统计、备份轮转、完整性、归档、VACUUM。"""

import json
import tempfile
import unittest
from pathlib import Path

from test_store import FakeEncoder

from astrbot_plugin_digital_memory_store import panel
from astrbot_plugin_digital_memory_store.storage import Store


class TestPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root, '', FakeEncoder)
        self.scope = self.store.scope('1', 's')
        self.store.capture(self.scope, 'old', '下个月十五号搬到杭州西湖区',
                           recorded_at='2025-01-01T10:00:00+00:00')
        self.store.capture(self.scope, 'new', '周末去公园跑步',
                           recorded_at='2026-11-01T10:00:00+00:00')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_stats_shape(self):
        data = panel.stats(self.root, self.store)
        self.assertEqual(data['captures'], 2)
        self.assertGreater(data['db']['bytes'], 0)
        self.assertGreater(data['per_message_bytes'], 0)
        self.assertIn('100', data['projections_5y'])
        self.assertIsInstance(data['warnings'], list)

    def test_backup_and_rotate(self):
        panel.create_backup(self.store)
        panel.create_backup(self.store)
        panel.create_backup(self.store)
        self.assertEqual(len(panel.list_backups(self.root)), 3)
        result = panel.rotate_backups(self.root, keep=1)
        self.assertEqual(result['remaining'], 1)
        self.assertEqual(len(panel.list_backups(self.root)), 1)

    def test_integrity_and_checkpoint(self):
        self.assertEqual(panel.integrity(self.store)['result'], 'ok')
        self.assertIn('result', panel.checkpoint(self.store))

    def test_archive_preview_and_export(self):
        preview = panel.archive(self.store, months=12, export=False)
        self.assertEqual(preview['count'], 1, '只有 2025 那条算旧数据')
        exported = panel.archive(self.store, months=12, export=True)
        self.assertEqual(exported['count'], 1)
        path = Path(exported['path'])
        self.assertTrue(path.is_file())
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[0]['content'], '下个月十五号搬到杭州西湖区')

    def test_vacuum_reports_sizes(self):
        result = panel.vacuum(self.store)
        self.assertIn('before_mb', result)
        self.assertIn('after_mb', result)


class TestRegistration(unittest.TestCase):
    def test_panel_web_apis_registered(self):
        from test_plugin import m

        class FakeContext:
            def __init__(self):
                self.routes = []

            def register_web_api(self, route, handler, methods, desc):
                self.routes.append((route, tuple(methods)))

        context = FakeContext()
        plugin = m.DigitalMemoryStore(context, {'enabled': True})
        try:
            routes = dict(context.routes)
            self.assertEqual(len(routes), 7)
            self.assertEqual(routes['/astrbot_plugin_digital_memory_store/panel/stats'], ('GET',))
            self.assertEqual(routes['/astrbot_plugin_digital_memory_store/panel/backup'], ('POST',))
        finally:
            plugin.pool.shutdown(wait=False)


if __name__ == '__main__':
    unittest.main()
