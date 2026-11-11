import unittest
from host_shadow.assess_soak import assess


class TestSoakAssessment(unittest.TestCase):
    def samples(self):
        return [{'ts': index * 300, 'db_exists': True, 'log_exists': True,
                 'observations': index // 12} for index in range(865)]

    def test_full_synthetic_window_is_not_real_platform_acceptance(self):
        result = assess(self.samples())
        self.assertTrue(result['telemetry_window_pass'])
        self.assertFalse(result['release_go'])
        self.assertIn('real_platform_identity', result['requires_external_evidence'])

    def test_two_distant_samples_do_not_fake_72h(self):
        result = assess([self.samples()[0], self.samples()[-1]])
        self.assertFalse(result['telemetry_window_pass'])
        self.assertIn('sampling_gap', result['reasons'])

    def test_short_no_activity_error_and_clock_rewind_fail(self):
        samples = self.samples()
        for kind in ('short', 'inactive', 'error', 'clock', 'reset'):
            rows = [dict(row) for row in samples]
            if kind == 'short': rows = rows[:-1]
            if kind == 'inactive':
                for row in rows: row['observations'] = 12
            if kind == 'error': rows[30]['db_error'] = 'locked'
            if kind == 'clock': rows[30]['ts'] = rows[29]['ts']
            if kind == 'reset': rows[-1]['observations'] = 0
            with self.subTest(kind=kind):
                self.assertFalse(assess(rows)['telemetry_window_pass'])

    def test_empty_malformed_and_nonfinite_timestamps_fail(self):
        for records in ([], [{'ts': None}], [{'ts': float('nan')}], [{'ts': True}]):
            self.assertFalse(assess(records)['telemetry_window_pass'])
        with self.assertRaises(ValueError):
            assess([], hours=0)


if __name__ == '__main__':
    unittest.main()
