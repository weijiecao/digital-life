"""R0 audit: explain B4 610 raw pairs vs 560 scored pairs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from .b4_fixture import B4Fixture
except ImportError:  # script mode: python evaluation/v1_5b_b4_pair_audit.py
    from b4_fixture import B4Fixture


def audit(root: str) -> dict:
    fixture = B4Fixture.load(root)
    status = Counter()
    by_relation = Counter()
    b2_relation = Counter()
    by_difficulty = Counter()
    excluded = []
    for pair in fixture.pair_gold.values():
        left = fixture.messages[pair["left_source_id"]]
        right = fixture.messages[pair["right_source_id"]]
        if left["scope_id"] != right["scope_id"]:
            status["excluded_cross_scope"] += 1
            excluded.append({"pair_id": pair["pair_id"], "reason": "cross_scope",
                             "relation": pair["relation"],
                             "difficulty": pair.get("difficulty", "")})
            continue
        b2_relation[pair["relation"]] += 1
        if pair["left_source_id"] not in fixture.event_by_source:
            status["excluded_unresolved"] += 1
            excluded.append({"pair_id": pair["pair_id"], "reason": "unresolved_no_gold_event",
                             "relation": pair["relation"],
                             "difficulty": pair.get("difficulty", "")})
            continue
        if pair["right_source_id"] not in fixture.event_by_source:
            status["excluded_unresolved"] += 1
            excluded.append({"pair_id": pair["pair_id"], "reason": "unresolved_no_gold_event",
                             "relation": pair["relation"],
                             "difficulty": pair.get("difficulty", "")})
            continue
        status["pairwise_scored"] += 1
        by_relation[pair["relation"]] += 1
        by_difficulty[pair.get("difficulty", "")] += 1
    return {"raw_pairs": len(fixture.pair_gold),
            "b2_evaluated_pairs": status["pairwise_scored"] + status["excluded_unresolved"],
            "pairwise_scored_pairs": status["pairwise_scored"],
            "excluded_cross_scope": status["excluded_cross_scope"],
            "excluded_unresolved": status["excluded_unresolved"],
            "status": dict(status),
            "b2_relation_counts": dict(b2_relation),
            "pairwise_relation_counts": dict(by_relation),
            "scored_difficulty_counts": dict(by_difficulty),
            "excluded": excluded}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = audit(args.root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "raw_pairs", "b2_evaluated_pairs", "pairwise_scored_pairs",
        "excluded_cross_scope", "excluded_unresolved",
        "status", "b2_relation_counts", "pairwise_relation_counts",
        "scored_difficulty_counts")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
