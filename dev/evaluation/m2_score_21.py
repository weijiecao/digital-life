"""Score the frozen 21-case trace against its frozen expected labels.

The gold here is the case generator's label, not an independently annotated
held-out set. Report that limitation with the numbers.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    trace = json.loads((ROOT.parent / "reports" / "r11_s2_frozen_trace_21.json")
                       .read_text(encoding="utf-8"))["cases"]
    raw_ok = 0
    guard_ok = 0
    contamination = 0
    by_category = Counter()
    for case in trace:
        expected = case["expected_relation"]
        predicted = case["model_label"]
        category = {"T01": "same_story_same_event",
                    "T02": "same_story_different_event",
                    "T03": "different_story",
                    "T04": "narrative_to_user",
                    "T05": "user_real_same_event"}.get(case["case_id"][:3], "unknown")
        by_category[category] += 1
        if predicted == expected:
            raw_ok += 1
        effective = predicted
        if predicted == "SAME_EVENT" and case["guard_action"] != "ALLOW":
            effective = "BLOCKED"
        if effective == expected:
            guard_ok += 1
        if (predicted == "SAME_EVENT" and case["guard_action"] == "ALLOW"
                and category == "narrative_to_user"):
            contamination += 1
    payload = {"cases": len(trace), "raw_event_correct": raw_ok,
               "guard_effective_correct": guard_ok,
               "contamination": contamination,
               "by_category": dict(by_category),
               "gold": "generator labels (not independent holdout)"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    out = ROOT.parent / "reports" / "m2_score_21.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
