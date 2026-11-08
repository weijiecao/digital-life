"""B4.5-2 FN attribution from a frozen B1/B2/B3 trace."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from b4_fixture import B4Fixture


def run(root: str, trace_path: str) -> dict:
    fixture = B4Fixture.load(root)
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))["trace"]
    by_source = {entry["source_id"]: entry for entry in trace}
    members = defaultdict(set)
    for entry in trace:
        members[entry["final_event"]].add(entry["source_id"])

    def dominant_gold(event_id):
        counts = defaultdict(int)
        for source_id in members[event_id]:
            gold = fixture.event_by_source.get(source_id)
            if gold:
                counts[gold] += 1
        return max(counts, key=counts.get) if counts else None

    event_gold = {event_id: dominant_gold(event_id) for event_id in members}
    by_scope = defaultdict(list)
    for source_id, row in fixture.messages.items():
        by_scope[row["scope_id"]].append(source_id)

    def source_attribution(source_id: str) -> dict:
        entry = by_source[source_id]
        gold = fixture.event_by_source.get(source_id)
        gold_candidates = [cid for cid in entry["candidates"]
                           if event_gold.get(cid) == gold]
        same_targets = [p["candidate_event_id"] for p in entry["proposals"]
                        if p["label"] == "SAME_EVENT"]
        same_to_gold = [cid for cid in same_targets if cid in gold_candidates]
        return {"b1_recalled": bool(gold_candidates),
                "b2_same_gold": bool(same_to_gold),
                "final_gold": event_gold.get(entry["final_event"]),
                "final_event": entry["final_event"]}

    fn_cases = []
    for scope, sources in by_scope.items():
        valid = [s for s in sources if fixture.event_by_source.get(s)]
        for left, right in combinations(valid, 2):
            if fixture.event_by_source[left] != fixture.event_by_source[right]:
                continue
            if by_source[left]["final_event"] == by_source[right]["final_event"]:
                continue
            a = source_attribution(left)
            b = source_attribution(right)
            if not a["b1_recalled"] and not b["b1_recalled"]:
                cause = "b1_not_recalled_either"
            elif not a["b1_recalled"] or not b["b1_recalled"]:
                cause = "b1_not_recalled_one_side"
            elif not a["b2_same_gold"] and not b["b2_same_gold"]:
                cause = "b2_no_same_to_gold"
            elif a["b2_same_gold"] or b["b2_same_gold"]:
                cause = "b3_or_provisional_fragmentation"
            else:
                cause = "undetermined"
            fn_cases.append({"left": left, "right": right,
                             "gold_event": fixture.event_by_source[left],
                             "cause": cause,
                             "left_trace": a, "right_trace": b})
    counts = Counter(case["cause"] for case in fn_cases)
    return {"suite": "v1.5b-b4.5-2-fn-attribution",
            "fn_pairs": len(fn_cases), "causes": dict(counts),
            "examples": fn_cases[:30]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run(args.root, args.trace)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("fn_pairs", "causes")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
