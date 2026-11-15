"""原话阈值配置的回归：默认 0.60、配置百分比生效、非法值回退。"""

import asyncio
import tempfile
import unittest

from .test_plugin import m
from .test_store import FakeEncoder

from astrbot_plugin_digital_memory_store.storage import Store


class TestSourceThresholdDefault(unittest.TestCase):
    def test_store_default_is_sixty_percent(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d, '', FakeEncoder)
            try:
                self.assertEqual(store.source_min_cosine, 0.60)
                self.assertEqual(store.source_min_lead, 0.10)
            finally:
                store.close()


class TestPluginThresholdConfig(unittest.TestCase):
    def _threshold(self, cfg):
        async def go(root):
            m.get_astrbot_data_path = lambda: root
            plugin = m.DigitalMemoryStore(None, cfg)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(plugin.pool, plugin._ensure_store)
            try:
                return plugin.store.source_min_cosine
            finally:
                await plugin.terminate()

        with tempfile.TemporaryDirectory() as root:
            return asyncio.run(go(root))

    def test_configured_percent_is_applied(self):
        value = self._threshold({'enabled': True, 'source_similarity_percent': 55})
        self.assertEqual(value, 0.55)

    def test_invalid_percent_falls_back_to_sixty(self):
        for bad in (999, 10, '80', True):
            value = self._threshold({'enabled': True, 'source_similarity_percent': bad})
            self.assertEqual(value, 0.60, bad)


if __name__ == '__main__':
    unittest.main()
