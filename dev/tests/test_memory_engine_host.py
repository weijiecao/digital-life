import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import MemoryEngine
from memory_engine.host_adapter import SimHostAdapter
from v1_5_consolidator.store import V15Store


class TestHostAdapter(unittest.TestCase):
    def test_handle_message_returns_packet(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            adapter = SimHostAdapter(MemoryEngine(store))
            result = adapter.handle_message(
                {"scope_id": "s", "source_seq": 1, "text": "周末去爬山"})
            self.assertTrue(result["ingest"]["ok"])
            self.assertEqual(len(result["packet"]["items"]), 1)
            store.close()

    def test_missing_fields_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            adapter = SimHostAdapter(MemoryEngine(store))
            self.assertFalse(adapter.ingest_event({"scope_id": "s"})["ok"])
            event = {"scope_id": "s", "source_seq": 1, "text": "茶"}
            first = adapter.ingest_event(event)
            second = adapter.ingest_event(event)
            self.assertEqual(first["event_id"], second["event_id"])
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM event_candidate").fetchone()[0], 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
