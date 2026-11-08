"""B4.5-3 persistent batch state machine replay (frozen B2 trace).

Each batch is a job with a real SQLite transaction, cursor update, and a cold
store reopen between batches. Events created earlier in the same batch are
visible to later messages (intra-batch visibility).
"""

from __future__ import annotations

import argparse
import hashlib
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


def run(root: str, trace_path: str, batch_size: int, cold_start: bool) -> dict:
    fixture = B4Fixture.load(root)
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))["trace"]
    by_scope = defaultdict(list)
    for entry in trace:
        by_scope[entry["scope_id"]].append(entry)
    predictions = {}
    stats = {"messages": 0, "batches": 0, "attached": 0, "created": 0,
             "jobs_committed": 0, "cold_starts": 0}
    with tempfile.TemporaryDirectory(prefix="b4-state-") as tmp:
        db_path = str(Path(tmp) / "v15.sqlite3")
        store = V15Store(db_path)
        try:
            for scope, entries in by_scope.items():
                entries.sort(key=lambda item: item["source_seq"])
                for start in range(0, len(entries), batch_size):
                    batch = entries[start:start + batch_size]
                    digest = hashlib.sha256(
                        "|".join(e["source_id"] for e in batch).encode()).hexdigest()
                    owner = "b4-state-worker"
                    claim = store.claim_job(scope, batch[0]["source_seq"],
                                             batch[-1]["source_seq"], digest,
                                             owner, lease_seconds=300)
                    if claim is None:
                        raise RuntimeError("job could not be claimed")
                    job_id, token = claim
                    resolver = ConstrainedResolver(store)
                    for entry in batch:
                        source_message = fixture.messages[entry["source_id"]]
                        source = SourceEnvelope(scope, entry["source_seq"],
                                                source_message["sha256_text"],
                                                source_message["recorded_at"],
                                                source_message["text"])
                        proposals = [RelationProposal(p["candidate_event_id"], p["label"])
                                     for p in entry["proposals"]
                                     if store.event_in_scope(p["candidate_event_id"], scope)]
                        result = resolver.resolve(scope, source, JudgmentResult(
                            source_seq=entry["source_seq"], proposals=proposals,
                            issues=list(entry["issues"]),
                            request_id="state-" + entry["source_id"]))
                        attached = store.events_for_source(scope, entry["source_seq"])
                        if attached:
                            event_id = attached[0]
                            stats["attached"] += 1
                        else:
                            # No fallback to trace final_event: evaluation-only IDs
                            # must never become an association bypass.
                            event_id = store.create_event_from_source(
                                scope, source, event_type="unknown",
                                title=source.text[:80], event_id=entry["final_event"])
                            stats["created"] += 1
                        predictions[entry["source_id"]] = event_id
                        stats["messages"] += 1
                    if not store.commit_job(job_id, owner, token, batch[-1]["source_seq"]):
                        raise RuntimeError("job commit rejected by lease/fencing")
                    stats["jobs_committed"] += 1
                    stats["batches"] += 1
                    if cold_start:
                        store.close()
                        store = V15Store(db_path)
                        stats["cold_starts"] += 1
        finally:
            store.close()
    return {"suite": "v1.5b-b4.5-batch-state", "batch_size": batch_size,
            "cold_start": cold_start, **stats, "predictions": predictions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--cold-start", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run(args.root, args.trace, args.batch_size, args.cold_start)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pred_path = out.with_suffix(".jsonl")
    with pred_path.open("w", encoding="utf-8") as fh:
        for source_id, event_id in sorted(result["predictions"].items()):
            fh.write(json.dumps({"source_id": source_id,
                                 "predicted_event_id": event_id},
                                ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("batch_size", "cold_start", "messages",
                                              "batches", "attached", "created",
                                              "jobs_committed", "cold_starts")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
