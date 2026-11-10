import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from memory_engine.cli import main


class TestMemoryEngineCli(unittest.TestCase):
    def test_ingest_search_context(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "v15.sqlite3")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "ingest", "--scope", "s", "--seq", "1",
                      "--text", "周末去爬山"])
            self.assertIn("event_id", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "search", "--scope", "s", "--query", "爬山"])
            self.assertIn("周末去爬山", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "context", "--scope", "s", "--query", "爬山"])
            packet = json.loads(out.getvalue())
            self.assertEqual(len(packet["items"]), 1)

    def test_health(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "v15.sqlite3")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "health"])
            self.assertEqual(json.loads(out.getvalue()), {"events": 0, "pending": 0})


if __name__ == "__main__":
    unittest.main()
