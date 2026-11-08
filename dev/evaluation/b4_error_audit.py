"""B4.4-1 final-cluster error audit.

This is a final-assignment audit, not a full B1/B2/B3 trace. It identifies
which pairs are false merges / false splits and which ambiguous sources were
forced into an event; root-cause labels are marked as inferred unless a trace
exists.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from b4_fixture import B4Fixture


def load_predictions(path: str) -> dict[str, str]:
    predictions = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        predictions[row["source_id"]] = row.get("predicted_event_id")
    return predictions


def audit(root: str, predictions_path: str) -> dict:
    fixture = B4Fixture.load(root)
    predictions = load_predictions(predictions_path)
    by_scope = defaultdict(list)
    for source_id, row in fixture.messages.items():
        by_scope[row["scope_id"]].append(source_id)
    fp_cases, fn_cases, ambiguous_cases = [], [], []
    for scope, sources in by_scope.items():
        for source in sources:
            gold = fixture.event_by_source.get(source)
            pred = predictions.get(source)
            if gold is None and pred is not None:
                ambiguous_cases.append({"source_id": source, "predicted_event_id": pred})
        valid = [s for s in sources if fixture.event_by_source.get(s)]
        for left, right in combinations(valid, 2):
            gold_same = fixture.event_by_source[left] == fixture.event_by_source[right]
            predicted_same = (predictions.get(left) is not None
                              and predictions.get(left) == predictions.get(right))
            if gold_same and not predicted_same:
                fn_cases.append({"left": left, "right": right,
                                 "gold_event": fixture.event_by_source[left],
                                 "left_pred": predictions.get(left),
                                 "right_pred": predictions.get(right)})
            elif not gold_same and predicted_same:
                fp_cases.append({"left": left, "right": right,
                                 "left_gold": fixture.event_by_source[left],
                                 "right_gold": fixture.event_by_source[right],
                                 "predicted_event": predictions.get(left)})
    fp_reasons = Counter()
    for case in fp_cases:
        types = {fixture.events[case["left_gold"]].get("event_type", "unknown"),
                 fixture.events[case["right_gold"]].get("event_type", "unknown")}
        fp_reasons["cross_event_" + "_".join(sorted(types))] += 1
    fn_reasons = Counter()
    for case in fn_cases:
        if case["left_pred"] is not None and case["left_pred"] == case["right_pred"]:
            fn_reasons["unexpected_same"] += 1
        elif case["left_pred"] is None or case["right_pred"] is None:
            fn_reasons["missing_prediction"] += 1
        else:
            fn_reasons["split_across_clusters"] += 1
    return {"suite": "v1.5b-b4.4-error-audit",
            "fp_pairs": len(fp_cases), "fn_pairs": len(fn_cases),
            "ambiguous_forced_attach": len(ambiguous_cases),
            "fp_reasons": dict(fp_reasons), "fn_reasons": dict(fn_reasons),
            "fp_examples": fp_cases[:12],
            "fn_examples": fn_cases[:20],
            "ambiguous_examples": ambiguous_cases[:20],
            "root_cause_note": ("final cluster assignment only; B1/B2/B3 per-message "
                                "traces are not available in this run")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = audit(args.root, args.predictions)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in (
        "fp_pairs", "fn_pairs", "ambiguous_forced_attach",
        "fp_reasons", "fn_reasons", "root_cause_note")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
