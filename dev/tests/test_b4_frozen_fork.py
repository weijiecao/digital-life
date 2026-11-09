import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evaluation.b4_frozen_replay import run


def _write_fixture(root: Path):
    messages = [
        {"source_id": "s1", "stream_seq": 1, "sha256_text": "a", "recorded_at": "t",
         "text": "first"},
        {"source_id": "s2", "stream_seq": 2, "sha256_text": "b", "recorded_at": "t",
         "text": "second"},
        {"source_id": "s3", "stream_seq": 3, "sha256_text": "c", "recorded_at": "t",
         "text": "third"},
    ]
    (root / "messages_interleaved.jsonl").write_text(
        "\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")
    for name in ("gold_events.jsonl", "message_gold.jsonl", "pair_gold.jsonl",
                 "retrieval_queries_input.jsonl", "retrieval_query_gold.jsonl"):
        (root / name).write_text("", encoding="utf-8")
    trace = {"trace": [
        {"scope_id": "s", "source_id": "s1", "source_seq": 1,
         "candidates": [], "proposals": [], "issues": [], "final_event": "old-E"},
        {"scope_id": "s", "source_id": "s2", "source_seq": 2,
         "candidates": [], "proposals": [], "issues": [], "final_event": "old-E"},
        {"scope_id": "s", "source_id": "s3", "source_seq": 3,
         "candidates": ["old-E"],
         "proposals": [{"candidate_event_id": "old-E", "label": "SAME_EVENT"}],
         "issues": [], "final_event": "old-F"},
    ]}
    (root / "trace.json").write_text(json.dumps(trace), encoding="utf-8")
    return root / "trace.json"


class TestFrozenFork(unittest.TestCase):
    def test_actual_replay_one_old_event_splits_and_defers(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            trace = _write_fixture(root)
            result = run(str(root), str(trace), batch_size=2)
            self.assertEqual(result["messages"], 3)
            self.assertEqual(result["created"], 3)
            self.assertEqual(result["attached"], 0)
            self.assertEqual(result["deferred_mapping"], 1)
            self.assertEqual(result["mapping_ambiguous"], 1)

    def test_reopen_and_repeat_are_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            trace = _write_fixture(root)
            db_path = str(root / "replay.sqlite3")
            first = run(str(root), str(trace), batch_size=2, db_path=db_path)
            self.assertEqual(first["messages"], 3)
            db = sqlite3.connect(db_path)
            evidence = db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0]
            mapping = db.execute("SELECT COUNT(*) FROM replay_map").fetchone()[0]
            db.close()
            second = run(str(root), str(trace), batch_size=2, db_path=db_path)
            self.assertEqual(second["messages"], 0)
            self.assertGreaterEqual(second["job_claim_failed"], 1)
            db = sqlite3.connect(db_path)
            try:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0],
                                 evidence)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM replay_map").fetchone()[0],
                                 mapping)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
