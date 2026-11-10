import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from memory_engine.shadow import ShadowRunner
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


class TestShadow(unittest.TestCase):
    def test_same_data_is_equal(self):
        with tempfile.TemporaryDirectory() as d:
            a = V15Store(str(Path(d) / "a.sqlite3"))
            b = V15Store(str(Path(d) / "b.sqlite3"))
            for store in (a, b):
                MemoryEngine(store).ingest(
                    "s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            result = ShadowRunner(MemoryEngine(a), MemoryEngine(b)).compare("s", "茶")
            self.assertTrue(result["same_items"])
            a.close()
            b.close()

    def test_different_data_is_diff(self):
        with tempfile.TemporaryDirectory() as d:
            a = V15Store(str(Path(d) / "a.sqlite3"))
            b = V15Store(str(Path(d) / "b.sqlite3"))
            MemoryEngine(a).ingest("s", SourceEnvelope("s", 1, "sha", "t", "茶"))
            MemoryEngine(b).ingest("s", SourceEnvelope("s", 1, "sha", "t", "咖啡"))
            result = ShadowRunner(MemoryEngine(a), MemoryEngine(b)).compare("s", "茶")
            self.assertFalse(result["same_items"])
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
