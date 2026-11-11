import json
import tempfile
import unittest
from pathlib import Path

from evaluation.b4_frozen_replay import run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evaluation" / "b4_testdata" / "Digital_Life_B4_TestData_v1"
TRACE = ROOT / "reports" / "v1_5b_b4_cluster_prov_trace.json"


class TestR0DSameDbReplay(unittest.TestCase):
    def test_same_db_replay_recovers_receipts_without_writes(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "replay.sqlite3")
            first = run(str(FIXTURE), str(TRACE), 8, db)
            second = run(str(FIXTURE), str(TRACE), 8, db)
            self.assertEqual(first["messages"], 340)
            self.assertEqual(second["scope_claim_failed"], 0)
            self.assertEqual(second["recovery_scan"], 340)
            self.assertEqual(second["receipt_hits"], 340)
            self.assertEqual(second["receipt_conflicts"], 0)
            self.assertEqual(second["messages"], 0)
            self.assertEqual(second["diff"], {})
            self.assertEqual(first["after"], second["before"])
            for scope, state in second["after"]["scopes"].items():
                self.assertEqual(state["duplicates"],
                                 {"evidence": 0, "receipts": 0, "decisions": 0})


if __name__ == "__main__":
    unittest.main()
