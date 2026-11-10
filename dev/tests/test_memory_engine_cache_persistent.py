import tempfile
import unittest
from pathlib import Path

from memory_engine.cache import PersistentContextCache
from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestPersistentContextCache(unittest.TestCase):
    def test_second_engine_uses_persisted_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            a = V15Store(path)
            b = V15Store(path)
            engine_a = MemoryEngine(a, cache=PersistentContextCache(a))
            engine_b = MemoryEngine(b, cache=PersistentContextCache(b))
            engine_a.ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            first = engine_a.build_context("s", "茶")
            self.assertEqual(len(first["items"]), 1)
            self.assertEqual(engine_a.cache.size(), 1)
            second = engine_b.build_context("s", "茶")
            self.assertEqual(first, second)
            b.delete_source("s", 1)
            self.assertEqual(engine_a.build_context("s", "茶")["items"], [])
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
