import subprocess
import sys
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

    def test_two_processes_share_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "v15.sqlite3")
            from v1_5_consolidator.contracts import SourceEnvelope
            from v1_5_consolidator.store import V15Store
            setup = V15Store(path)
            MemoryEngine(setup).ingest(
                "s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            setup.close()
            code = """
import sys
sys.path.insert(0, sys.argv[2])
from memory_engine.cache import PersistentContextCache
from memory_engine.engine import MemoryEngine
from v1_5_consolidator.store import V15Store
s = V15Store(sys.argv[1])
e = MemoryEngine(s, cache=PersistentContextCache(s))
p = e.build_context('s', '茶')
assert len(p['items']) == 1
s.close()
"""
            procs = [subprocess.Popen([sys.executable, "-c", code, path,
                                      str(Path(__file__).resolve().parents[1])],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True) for _ in range(4)]
            for proc in procs:
                _, err = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 0, err)

    def test_prune_keeps_recent_rows(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            cache = PersistentContextCache(store)
            for i in range(3):
                cache.put(("s", "q%d" % i), 1, {"i": i})
            self.assertEqual(cache.size(), 3)
            self.assertEqual(cache.prune(2), 1)
            self.assertEqual(cache.size(), 2)
            store.close()


if __name__ == "__main__":
    unittest.main()
