import unittest

from evaluation.closed_loop_accept import run


class TestClosedLoop(unittest.TestCase):
    def test_automatic_retry_restart_cache_budget_and_long_run(self):
        result = run()
        self.assertTrue(result["pass"], result["checks"])


if __name__ == "__main__":
    unittest.main()
