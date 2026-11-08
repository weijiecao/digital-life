"""Deterministic gate effect on a frozen B2/R12 trace.

Uses the exact trace from one cluster run; it does not call a model. For each
SAME_EVENT proposal, compare the gate decision against whether the proposed
target event actually contains the source's gold event.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from b4_fixture import B4Fixture
from v1_5_consolidator.evidence_gate import EvidenceGate, GateInput
from v1_5_consolidator.relation_judge import RelationProposal


def run(root: str, trace_path: str) -> dict:
    fixture = B4Fixture.load(root)
    payload = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    trace = payload["trace"]
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

    gate = EvidenceGate()
    counts = defaultdict(int)
    examples = []
    for entry in trace:
        same = [p for p in entry["proposals"] if p["label"] == "SAME_EVENT"]
        if not same:
            continue
        source = fixture.messages[entry["source_id"]]
        source_gold = fixture.event_by_source.get(entry["source_id"])
        candidates = []
        for event_id in entry["candidates"]:
            prior = [fixture.messages[s]["text"] for s in members[event_id]
                     if int(fixture.messages[s]["stream_seq"]) < int(source["stream_seq"])]
            candidates.append({"event_id": event_id, "title": " ".join(prior)[:80],
                               "anchor_text": " ".join(prior)})
        proposals = [RelationProposal(p["candidate_event_id"], p["label"]) for p in same]
        decision = gate.evaluate(GateInput(
            scope_id=entry["scope_id"], source_id=entry["source_id"],
            source_text=source["text"], candidates=candidates,
            relation_proposals=proposals))
        target = decision.target_event_id or same[0]["candidate_event_id"]
        target_gold = dominant_gold(target)
        correct = source_gold is not None and source_gold == target_gold
        key = f"{decision.action.lower()}_{'correct' if correct else 'incorrect'}"
        counts[key] += 1
        if len(examples) < 20 and decision.action != "ALLOW":
            examples.append({"source_id": entry["source_id"],
                             "source_gold": source_gold, "target": target,
                             "target_gold": target_gold,
                             "action": decision.action,
                             "reason_codes": list(decision.reason_codes)})
    return {"suite": "v1.5b-b4.4-2-gate-paired", "same_proposals": sum(counts.values()),
            "counts": dict(counts), "examples": examples}


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
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
