import unittest

from v1_5_consolidator.evidence_gate import EvidenceGate, GateInput
from v1_5_consolidator.relation_judge import RelationProposal


class TestEvidenceGate(unittest.TestCase):
    def setUp(self):
        self.gate = EvidenceGate()
        self.candidates = [
            {"event_id": "evt_a", "title": "十月去青岛", "anchor_text": "计划十月去青岛玩"},
            {"event_id": "evt_b", "title": "十二月去青岛", "anchor_text": "十二月还想去青岛"},
        ]

    def test_no_same_is_abstained(self):
        result = self.gate.evaluate(GateInput("s", "m", "无关", self.candidates, [
            RelationProposal("evt_a", "RELATED_CONTEXT")]))
        self.assertEqual(result.action, "ABSTAIN")
        self.assertIn("no_same_proposal", result.reason_codes)

    def test_multiple_same_is_abstained(self):
        result = self.gate.evaluate(GateInput("s", "m", "青岛", self.candidates, [
            RelationProposal("evt_a", "SAME_EVENT"),
            RelationProposal("evt_b", "SAME_EVENT")]))
        self.assertEqual(result.action, "ABSTAIN")
        self.assertIn("multiple_same_targets", result.reason_codes)

    def test_weak_reference_without_anchor_is_abstained(self):
        result = self.gate.evaluate(GateInput("s", "m", "那个后来呢", self.candidates, [
            RelationProposal("evt_a", "SAME_EVENT")]))
        self.assertEqual(result.action, "ABSTAIN")
        self.assertIn("weak_reference_without_anchor", result.reason_codes)

    def test_shared_anchor_allows(self):
        result = self.gate.evaluate(GateInput("s", "m", "青岛的酒店", self.candidates, [
            RelationProposal("evt_a", "SAME_EVENT")]))
        self.assertEqual(result.action, "ALLOW")
        self.assertEqual(result.target_event_id, "evt_a")

    def test_missing_candidate_is_rejected(self):
        result = self.gate.evaluate(GateInput("s", "m", "青岛", self.candidates, [
            RelationProposal("evt_missing", "SAME_EVENT")]))
        self.assertEqual(result.action, "REJECT")
        self.assertIn("candidate_missing", result.reason_codes)

    def test_explicit_date_conflict_is_rejected(self):
        result = self.gate.evaluate(GateInput("s", "m", "十二月去青岛", self.candidates, [
            RelationProposal("evt_a", "SAME_EVENT")]))
        self.assertEqual(result.action, "REJECT")
        self.assertIn("explicit_date_conflict", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
