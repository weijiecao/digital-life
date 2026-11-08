"""Deterministic batch-state replay using a frozen B2/R12 trace.

This isolates batch boundaries / commit visibility from model call variance:
the same B2 proposals are replayed under batch=8/20/40. Events created in a
batch are not visible as candidates until the next batch commits.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from b4_fixture import B4Fixture
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


def run(root: str, trace_path: str, batch_size: int,
        visibility: str = "deferred") -> dict:
    fixture = B4Fixture.load(root)
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))["trace"]
    by_scope = defaultdict(list)
    for entry in trace:
        by_scope[entry["scope_id"]].append(entry)
    predictions = {}
    stats = {"messages": 0, "batches": 0, "attached": 0, "created": 0,
             "attached_cross_batch": 0}
    with tempfile.TemporaryDirectory(prefix="b4-batch-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        resolver = ConstrainedResolver(store)
        try:
            for scope, entries in by_scope.items():
                entries.sort(key=lambda item: item["source_seq"])
                event_map: dict[str, str] = {}
                for start in range(0, len(entries), batch_size):
                    batch = entries[start:start + batch_size]
                    pending: dict[str, str] = {}
                    for entry in batch:
                        source_message = fixture.messages[entry["source_id"]]
                        source = SourceEnvelope(scope, entry["source_seq"],
                                                source_message["sha256_text"],
                                                source_message["recorded_at"],
                                                source_message["text"])
                        proposals = []
                        for p in entry["proposals"]:
                            mapped = event_map.get(p["candidate_event_id"])
                            if mapped:
                                proposals.append(RelationProposal(mapped, p["label"]))
                        judgment = JudgmentResult(source_seq=entry["source_seq"],
                                                  proposals=proposals,
                                                  issues=list(entry["issues"]),
                                                  request_id="batch-" + entry["source_id"])
                        result = resolver.resolve(scope, source, judgment)
                        attached = store.events_for_source(scope, entry["source_seq"])
                        if attached:
                            event_id = attached[0]
                            stats["attached"] += 1
                            if event_map.get(entry["final_event"]) != event_id:
                                stats["attached_cross_batch"] += 1
                        else:
                            event_id = store.create_event_from_source(
                                scope, source, event_type="unknown",
                                title=source.text[:80])
                            stats["created"] += 1
                        pending[entry["final_event"]] = event_id
                        if visibility == "intra":
                            event_map[entry["final_event"]] = event_id
                        predictions[entry["source_id"]] = event_id
                        stats["messages"] += 1
                    event_map.update(pending)
                    stats["batches"] += 1
        finally:
            store.close()
    return {"suite": "v1.5b-b4.5-batch-replay", "batch_size": batch_size,
            "visibility": visibility, **stats, "predictions": predictions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--visibility", choices=("deferred", "intra"), default="deferred")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run(args.root, args.trace, args.batch_size, args.visibility)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pred_path = out.with_suffix(".jsonl")
    with pred_path.open("w", encoding="utf-8") as fh:
        for source_id, event_id in sorted(result["predictions"].items()):
            fh.write(json.dumps({"source_id": source_id,
                                 "predicted_event_id": event_id},
                                ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("batch_size", "visibility", "messages",
                                              "batches", "attached", "created",
                                              "attached_cross_batch")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
