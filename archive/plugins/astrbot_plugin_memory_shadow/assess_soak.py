"""Assess collector coverage only; never turn samples into platform/user proof."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def assess(records: list[dict], hours: float = 72, max_gap: float = 600) -> dict:
    if not math.isfinite(hours) or hours <= 0 or not math.isfinite(max_gap) or max_gap <= 0:
        raise ValueError('hours and max_gap must be finite and positive')
    reasons = []
    if len(records) < 2:
        reasons.append('insufficient_samples')
    stamps = [row.get('ts') for row in records]
    valid_ts = all(isinstance(ts, (int, float)) and not isinstance(ts, bool)
                   and math.isfinite(ts) for ts in stamps)
    if not valid_ts:
        reasons.append('invalid_timestamps')
    gaps = [b - a for a, b in zip(stamps, stamps[1:])] if valid_ts else []
    duration = stamps[-1] - stamps[0] if valid_ts and len(stamps) >= 2 else 0
    if any(gap <= 0 for gap in gaps):
        reasons.append('non_increasing_timestamps')
    if duration < hours * 3600:
        reasons.append('window_too_short')
    if any(gap > max_gap for gap in gaps):
        reasons.append('sampling_gap')
    if any(row.get('db_exists') is not True or row.get('log_exists') is not True
           or row.get('db_error') for row in records):
        reasons.append('collector_errors_or_missing_files')
    counts = [row.get('observations') for row in records]
    valid_counts = all(isinstance(count, int) and not isinstance(count, bool)
                       and count >= 0 for count in counts)
    if not valid_counts:
        reasons.append('invalid_observation_counts')
    elif len(counts) < 2 or counts[-1] <= counts[0]:
        reasons.append('no_observed_activity')
    if valid_counts and any(b < a for a, b in zip(counts, counts[1:])):
        reasons.append('observation_log_reset')
    return {'telemetry_window_pass': not reasons, 'samples': len(records),
            'duration_hours': duration / 3600, 'max_gap_seconds': max(gaps, default=0),
            'reasons': reasons, 'release_go': False,
            'requires_external_evidence': ['real_platform_identity', 'host_errors_and_restart_log',
                                           'request_reply_unchanged', 'user_effect_evaluation'],
            'boundary': 'Collector timestamps and DB/log availability do not prove real platform or user benefit.'}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--hours', type=float, default=72)
    parser.add_argument('--max-gap', type=float, default=600)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        records = [json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines() if line.strip()]
        if not all(isinstance(row, dict) for row in records):
            raise ValueError('Each sample must be a JSON object')
        result = assess(records, args.hours, args.max_gap)
    except (OSError, ValueError, TypeError) as exc:
        result = {'telemetry_window_pass': False, 'release_go': False, 'error': str(exc)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['telemetry_window_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
