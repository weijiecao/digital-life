import tempfile
import unittest
from pathlib import Path

from memory_engine.worker import ReadyWorker
from v1_5_consolidator.store import V15Store


class TestReadyWorker(unittest.TestCase):
    def test_worker_consumes_queue_once(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            store.enqueue_ready("s", 2, "dep")
            store.enqueue_ready("s", 1, "dep")
            seen = []
            result = ReadyWorker(store).run_once(
                "s", lambda scope, seq: seen.append((scope, seq)) or {"outcome": "DONE"})
            self.assertEqual(result, {"processed": 2, "failed": 0})
            self.assertEqual(seen, [("s", 1), ("s", 2)])
            self.assertEqual(store.poll_ready("s"), [])
            store.close()

    def test_worker_retry_keeps_ready(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            store.enqueue_ready("s", 1, "dep")
            def fail(scope, seq):
                raise RuntimeError("model unavailable")
            result = ReadyWorker(store).run_once("s", fail)
            self.assertEqual(result["failed"], 1)
            self.assertEqual([r["source_seq"] for r in store.poll_ready("s")], [1])
            store.close()


if __name__ == "__main__":
    unittest.main()
