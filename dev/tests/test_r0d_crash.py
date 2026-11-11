import tempfile
import unittest
from pathlib import Path

from evaluation.r0d_crash_replay import run_case


class TestR0DCrashRecovery(unittest.TestCase):
    def test_before_and_after_commit_recover_without_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            for crash_at in ("before_commit", "after_commit"):
                result = run_case(crash_at, crash_at, Path(d))
                self.assertEqual(result["crash"]["exit"], 17)
                self.assertEqual(result["after"]["receipts"], 340)
                self.assertEqual(result["after"]["evidence"], 340)
                self.assertEqual(result["resume"]["scope_claim_failed"], 0)
                self.assertEqual(set(result["after"]["frontier"].values()), {34})
                if crash_at == "before_commit":
                    self.assertEqual(result["before_resume"]["receipts"], 0)
                    self.assertEqual(result["resume"]["messages"], 340)
                else:
                    self.assertEqual(result["before_resume"]["receipts"], 1)
                    self.assertEqual(result["resume"]["already_receipted"], 1)
                    self.assertEqual(result["resume"]["messages"], 339)


if __name__ == "__main__":
    unittest.main()
