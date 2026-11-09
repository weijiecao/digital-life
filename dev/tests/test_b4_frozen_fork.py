import json
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


if __name__ == "__main__":
    unittest.main()
