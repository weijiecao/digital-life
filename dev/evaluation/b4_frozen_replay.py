"""Frozen-decision replay with fresh event IDs and persisted old->new mapping.

No model calls. The input trace supplies decisions; missing provenance is
recorded as NOT_APPLIED. New events get independent IDs; old trace IDs are
never reused as event IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .b4_fixture import B4Fixture
except ImportError:
    from b4_fixture import B4Fixture
from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


def _load_map(store, scope):
    result = defaultdict(set)
    for old, new in store.db.execute(
            "SELECT old_event_id,event_id FROM replay_map WHERE scope_id=?", (scope,)):
        result[old].add(new)
    return result


def _add_map(store, scope, old, new):
    store.db.execute("""INSERT OR IGNORE INTO replay_map(scope_id,old_event_id,event_id)
        VALUES(?,?,?)""", (scope, old, new))


def mapping_status(mapping, old_event_id):
    targets = mapping.get(old_event_id, set())
    if not targets:
        return "MISSING"
    if len(targets) == 1:
        return "UNIQUE"
    return "AMBIGUOUS_SPLIT"


def run(root: str, trace_path: str, batch_size: int) -> dict:
    fixture = B4Fixture.load(root)
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))["trace"]
    by_scope = defaultdict(list)
    for entry in trace:
        by_scope[entry["scope_id"]].append(entry)
    predictions = {}
    stats = Counter()
    with tempfile.TemporaryDirectory(prefix="b4-frozen-") as tmp:
        db_path = str(Path(tmp) / "v15.sqlite3")
        store = V15Store(db_path)
        store.db.execute("""CREATE TABLE IF NOT EXISTS replay_map(
            scope_id TEXT NOT NULL, old_event_id TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(scope_id,old_event_id,event_id))""")
        store.db.commit()
        try:
            for scope, entries in by_scope.items():
                entries.sort(key=lambda item: item["source_seq"])
                mapping = _load_map(store, scope)
                for start in range(0, len(entries), batch_size):
                    batch = entries[start:start + batch_size]
                    digest = hashlib.sha256(
                        "|".join(e["source_id"] for e in batch).encode()).hexdigest()
                    claim = store.claim_job(scope, batch[0]["source_seq"],
                                             batch[-1]["source_seq"], digest,
                                             "frozen-replay", 300)
                    if claim is None:
                        stats["job_claim_failed"] += 1
                        continue
                    job_id, token = claim
                    resolver = ConstrainedResolver(store)
                    for entry in batch:
                        msg = fixture.messages[entry["source_id"]]
                        source = SourceEnvelope(scope, entry["source_seq"],
                                                msg["sha256_text"], msg["recorded_at"],
                                                msg["text"])
                        proposals = []
                        for p in entry.get("proposals", []):
                            status = mapping_status(mapping, p["candidate_event_id"])
                            candidates = mapping.get(p["candidate_event_id"], set())
                            if status == "UNIQUE":
                                proposals.append(RelationProposal(next(iter(candidates)), p["label"]))
                            elif status == "AMBIGUOUS_SPLIT":
                                stats["mapping_ambiguous"] += 1
                            else:
                                stats["mapping_missing"] += 1
                        prov_data = entry.get("source_provenance") or {}
                        provenance = NarrativeProvenance(**prov_data) if prov_data else NarrativeProvenance()
                        judgment = JudgmentResult(
                            source_seq=entry["source_seq"], proposals=proposals,
                            issues=list(entry.get("issues", [])),
                            request_id="frozen-" + entry["source_id"],
                            provenance=provenance,
                            provenance_present=bool(prov_data))
                        resolver.resolve(scope, source, judgment)
                        attached = store.events_for_source(scope, source.source_seq)
                        if attached:
                            new_id = attached[0]
                            stats["attached"] += 1
                        else:
                            new_id = "evt_replay_" + uuid.uuid4().hex
                            store.create_event_from_source(
                                scope, source, event_type="unknown",
                                title=source.text[:80], event_id=new_id,
                                provenance=prov_data or None)
                            stats["created"] += 1
                        _add_map(store, scope, entry["final_event"], new_id)
                        mapping.setdefault(entry["final_event"], set()).add(new_id)
                        predictions[entry["source_id"]] = new_id
                        stats["messages"] += 1
                    if not store.commit_job(job_id, "frozen-replay", token,
                                            batch[-1]["source_seq"]):
                        stats["job_commit_failed"] += 1
                    store.db.commit()
        finally:
            store.close()
    for key in ("messages", "attached", "created", "mapping_ambiguous",
                "mapping_missing", "job_claim_failed", "job_commit_failed"):
        stats.setdefault(key, 0)
    return {"suite": "v1.5b-frozen-replay", "batch_size": batch_size,
            **dict(stats), "predictions": predictions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = run(args.root, args.trace, args.batch_size)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pred = out.with_suffix(".jsonl")
    with pred.open("w", encoding="utf-8") as fh:
        for sid, eid in sorted(result["predictions"].items()):
            fh.write(json.dumps({"source_id": sid, "predicted_event_id": eid},
                                ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("batch_size", "messages", "attached",
                                              "created", "mapping_ambiguous",
                                              "mapping_missing",
                                              "job_claim_failed", "job_commit_failed")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
