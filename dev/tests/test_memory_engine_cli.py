import contextlib
import importlib.util
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

    @unittest.skipUnless(importlib.util.find_spec("tiktoken"), "tiktoken not installed")
    def test_rendered_context_cli(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "v15.sqlite3")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["--db", db, "ingest", "--scope", "s", "--seq", "1",
                      "--text", "我喜欢乌龙茶。我喜欢乌龙茶。"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "context", "--scope", "s", "--query", "乌龙茶",
                      "--rendered", "--budget-tokens", "150"])
            packet = json.loads(out.getvalue())
            self.assertTrue(packet["budget_enforced"])
            self.assertLessEqual(packet["rendered_tokens"], 150)
            self.assertIn("summary:", packet["rendered"])

    def test_history_command(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "v15.sqlite3")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "ingest", "--scope", "s", "--seq", "1",
                      "--text", "茶"])
            event_id = json.loads(out.getvalue())["event_id"]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "history", "--scope", "s",
                      "--event-id", event_id])
            history = json.loads(out.getvalue())
            self.assertEqual(history["events"], [event_id])
            self.assertEqual(history["relations"], [])

    def test_revise_and_withdraw_commands(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "v15.sqlite3")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "ingest", "--scope", "s", "--seq", "1",
                      "--text", "杭州"])
            old = json.loads(out.getvalue())["event_id"]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "revise", "--scope", "s", "--old-seq", "1",
                      "--new-seq", "2", "--text", "上海"])
            new = json.loads(out.getvalue())["event_id"]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "search", "--scope", "s", "--query", "上海"])
            self.assertIn(new, out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "withdraw", "--scope", "s", "--event-id", new])
            self.assertTrue(json.loads(out.getvalue())["withdrawn"])
            self.assertNotEqual(old, new)

    def test_health(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "v15.sqlite3")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--db", db, "health"])
            self.assertEqual(json.loads(out.getvalue()), {"events": 0, "pending": 0})


if __name__ == "__main__":
    unittest.main()
