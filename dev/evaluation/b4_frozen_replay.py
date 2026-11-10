"""Frozen-decision replay with fresh event IDs and persisted old->new mapping.

No model calls. The input trace supplies decisions; missing provenance is
recorded as NOT_APPLIED. New events get independent IDs; old trace IDs are
never reused as event IDs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .b4_fixture import B4Fixture
except ImportError:
    from b4_fixture import B4Fixture
from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.provenance_guard import ProvenanceGuard
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


@dataclass(frozen=True)
class MappedProposal:
    old_event_id: str
    original_label: str
    mapping_status: str
    candidate_event_ids: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappingResult:
    proposals: tuple[MappedProposal, ...]
    safe_to_resolve: bool
    reason_codes: tuple[str, ...] = ()


def map_proposals(entry: dict, mapping: dict) -> MappingResult:
    """Map every original proposal; never silently drop an incomplete one."""
    mapped = []
    reasons = []
    safe = True
    for proposal in entry.get("proposals", []):
        old = proposal["candidate_event_id"]
        status = mapping_status(mapping, old)
        targets = tuple(sorted(mapping.get(old, set())))
        if status != "UNIQUE":
            safe = False
            reasons.append("%s:%s" % (old, status.lower()))
        mapped.append(MappedProposal(
            old_event_id=old, original_label=proposal["label"],
            mapping_status=status, candidate_event_ids=targets,
            reason_codes=tuple(proposal.get("reason_codes", []))))
    return MappingResult(tuple(mapped), safe, tuple(reasons))


def run(root: str, trace_path: str, batch_size: int, db_path: str | None = None,
        guard_policy: str = "abstain") -> dict:
    fixture = B4Fixture.load(root)
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))["trace"]
    by_scope = defaultdict(list)
    for entry in trace:
        by_scope[entry["scope_id"]].append(entry)
    predictions = {}
    stats = Counter()
    frontiers = {}
    lease_owner = "frozen-" + uuid.uuid4().hex
    temp_dir = tempfile.TemporaryDirectory(prefix="b4-frozen-") if db_path is None else None
    with (temp_dir if temp_dir is not None else contextlib.nullcontext()):
        if db_path is None:
            db_path = str(Path(temp_dir.name) / "v15.sqlite3")
        store = V15Store(db_path)
        store.db.execute("""CREATE TABLE IF NOT EXISTS replay_map(
            scope_id TEXT NOT NULL, old_event_id TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(scope_id,old_event_id,event_id))""")
        store.db.commit()
        try:
            for scope, entries in by_scope.items():
                entries.sort(key=lambda item: item["source_seq"])
                store.register_manifest(scope, [(e["source_seq"], e["source_id"])
                                                for e in entries])
                mapping = _load_map(store, scope)
                lease_token = store.claim_scope(scope, lease_owner, ttl=300)
                if lease_token is None:
                    stats["scope_claim_failed"] += 1
                    continue
                for start in range(0, len(entries), batch_size):
                    batch = entries[start:start + batch_size]
                    if not store.verify_scope(scope, lease_owner, lease_token):
                        stats["fenced"] += 1
                        break
                    digest = hashlib.sha256(
                        "|".join(e["source_id"] for e in batch).encode()).hexdigest()
                    claim = store.claim_job(scope, batch[0]["source_seq"],
                                             batch[-1]["source_seq"], digest,
                                             "frozen-replay", 300)
                    if claim is None:
                        stats["job_claim_failed"] += 1
                        continue
                    job_id, token = claim
                    resolver = ConstrainedResolver(
                        store, provenance_guard=ProvenanceGuard(guard_policy))
                    for entry in batch:
                        msg = fixture.messages[entry["source_id"]]
                        source = SourceEnvelope(scope, entry["source_seq"],
                                                msg["sha256_text"], msg["recorded_at"],
                                                msg["text"])
                        existing_receipt = store.receipt_for_source(scope, source.source_seq)
                        if (existing_receipt is not None
                                and existing_receipt["input_hash"] == source.source_hash):
                            stats["already_receipted"] += 1
                            continue
                        prov_data = entry.get("source_provenance") or {}
                        provenance = NarrativeProvenance(**prov_data) if prov_data else NarrativeProvenance()
                        mapped = map_proposals(entry, mapping)
                        for item in mapped.proposals:
                            if item.mapping_status == "AMBIGUOUS_SPLIT":
                                stats["mapping_ambiguous"] += 1
                            elif item.mapping_status == "MISSING":
                                stats["mapping_missing"] += 1
                        if not mapped.safe_to_resolve:
                            stats["deferred_mapping"] += 1
                            new_id = "evt_replay_" + uuid.uuid4().hex
                            with store.unit_of_work(scope, lease_owner, lease_token):
                                store.create_event_from_source(
                                    scope, source, event_type="unknown",
                                    title=source.text[:80], event_id=new_id,
                                    provenance=prov_data or None)
                                store.save_pending(
                                    scope, source.source_seq,
                                    [{"candidate_event_id": p.old_event_id,
                                      "label": p.original_label,
                                      "mapping_status": p.mapping_status,
                                      "candidate_event_ids": list(p.candidate_event_ids)}
                                     for p in mapped.proposals],
                                    {p.old_event_id: list(p.candidate_event_ids)
                                     for p in mapped.proposals},
                                    ",".join(mapped.reason_codes) or "MAPPING_INCOMPLETE",
                                    source=source, provenance=provenance)
                                _add_map(store, scope, entry["final_event"], new_id)
                                store.record_receipt(scope, source.source_seq,
                                                     "DEFER_MAPPING", source.source_hash)
                            stats["created"] += 1
                            mapping.setdefault(entry["final_event"], set()).add(new_id)
                            predictions[entry["source_id"]] = new_id
                            stats["messages"] += 1
                            continue
                        proposals = [
                            RelationProposal(item.candidate_event_ids[0], item.original_label)
                            for item in mapped.proposals
                            if item.mapping_status == "UNIQUE"]
                        judgment = JudgmentResult(
                            source_seq=entry["source_seq"], proposals=proposals,
                            issues=list(entry.get("issues", [])),
                            request_id="frozen-" + entry["source_id"],
                            provenance=provenance,
                            provenance_present=bool(prov_data))
                        with store.unit_of_work(scope, lease_owner, lease_token):
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
                            store.record_receipt(scope, source.source_seq,
                                                 "ATTACHED" if attached else "CREATED",
                                                 source.source_hash)
                        mapping.setdefault(entry["final_event"], set()).add(new_id)
                        predictions[entry["source_id"]] = new_id
                        stats["messages"] += 1
                    frontier = store.contiguous_frontier(scope)
                    if not store.commit_job(job_id, "frozen-replay", token, frontier,
                                            scope_owner=lease_owner,
                                            scope_token=lease_token):
                        stats["job_commit_failed"] += 1
                    store.db.commit()
                frontiers[scope] = store.contiguous_frontier(scope)
        finally:
            store.close()
    for key in ("messages", "attached", "created", "mapping_ambiguous",
                "mapping_missing", "deferred_mapping", "job_claim_failed",
                "job_commit_failed", "scope_claim_failed", "fenced",
                "already_receipted"):
        stats.setdefault(key, 0)
    return {"suite": "v1.5b-frozen-replay", "batch_size": batch_size,
            **dict(stats), "frontiers": frontiers, "predictions": predictions}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--guard-policy", default="abstain",
                        choices=["abstain", "allow_user_real"])
    args = parser.parse_args()
    result = run(args.root, args.trace, args.batch_size, args.db, args.guard_policy)
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
                                              "mapping_missing", "deferred_mapping",
                                              "scope_claim_failed", "fenced",
                                              "already_receipted",
                                              "job_claim_failed", "job_commit_failed")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
