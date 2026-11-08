import unittest
from pathlib import Path

from evaluation.b4_fixture import B4Fixture
from evaluation.v1_5b_b4_pair_audit import audit


ROOT = Path(__file__).resolve().parents[1] / "evaluation" / "b4_testdata" / "Digital_Life_B4_TestData_v1"


class TestB4Fixture(unittest.TestCase):
    def test_fixture_counts_and_gold_separation(self):
        fixture = B4Fixture.load(ROOT)
        self.assertEqual(len(fixture.messages), 340)
        self.assertEqual(len(fixture.events), 80)
        self.assertEqual(len(fixture.pair_gold), 610)
        visible = (ROOT / "messages_interleaved.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("gold_event_id", visible)
        self.assertNotIn("gold_action", visible)

    def test_fixture_relation_counts(self):
        fixture = B4Fixture.load(ROOT)
        counts = {}
        for pair in fixture.pair_gold.values():
            counts[pair["relation"]] = counts.get(pair["relation"], 0) + 1
        self.assertEqual(counts["SAME_EVENT"], 200)
        self.assertEqual(counts["RELATED"], 150)
        self.assertEqual(counts["DIFFERENT_EVENT"], 200)
        self.assertEqual(counts["UNRESOLVED"], 60)

    def test_pair_audit_denominators(self):
        result = audit(ROOT)
        self.assertEqual(result["raw_pairs"], 610)
        self.assertEqual(result["b2_evaluated_pairs"], 560)
        self.assertEqual(result["pairwise_scored_pairs"], 500)
        self.assertEqual(result["excluded_cross_scope"], 50)
        self.assertEqual(result["excluded_unresolved"], 60)


if __name__ == "__main__":
    unittest.main()
