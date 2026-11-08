"""R12 end-to-end cluster replay on the synthetic B4 fixture.

For each message in stream order: B1 local candidate retrieval -> B2 relation
judge -> B3 resolver. If no candidate is accepted, a new provisional event is
created. Gold labels are never passed to the retriever or judge.
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
from v1_5_consolidator.candidate_retriever import (EventAnchor,
                                                   HybridCandidateIndex,
                                                   LexicalCandidateIndex)
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.evidence_gate import EvidenceGate, GateInput
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.relation_judge import RelationJudge, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store

CANDIDATE_TOP_K = 5


def run(root: str, api_key_file: str, model: str, max_scopes: int = 0,
        bge_model: str = "", batch_size: int = 8, use_gate: bool = False) -> dict:
    fixture = B4Fixture.load(root)
    provider = OpenAIProvider("https://api.deepseek.com/v1", model,
                              Path(api_key_file).read_text(encoding="utf-8").strip())
    gate = EvidenceGate() if use_gate else None
    encoder = None
    if bge_model:
        from sentence_transformers import SentenceTransformer
        encoder = SentenceTransformer(bge_model, device="cpu")
    scopes = sorted({m["scope_id"] for m in fixture.messages.values()})
    if max_scopes:
        scopes = scopes[:max_scopes]
    predictions = {}
    trace = []
    stats = {"messages": 0, "batches": 0, "attached": 0, "created": 0, "invalid": 0,
             "gate_allow": 0, "gate_abstain": 0, "gate_reject": 0}
    with tempfile.TemporaryDirectory(prefix="b4-r12-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        gateway = ContextGateway(store, deny_external=False)
        judge = RelationJudge(gateway, provider)
        resolver = ConstrainedResolver(store)
        try:
            for scope in scopes:
                messages = sorted([m for m in fixture.messages.values()
                                   if m["scope_id"] == scope],
                                  key=lambda item: int(item["stream_seq"]))
                event_texts: dict[str, list[str]] = defaultdict(list)
                event_ids: list[str] = []
                recent_context: list[SourceEnvelope] = []
                for index, message in enumerate(messages, start=1):
                    source = SourceEnvelope(scope, index, message["sha256_text"],
                                            message["recorded_at"], message["text"])
                    candidates = []
                    if event_ids:
                        index_obj = (HybridCandidateIndex(encoder) if encoder is not None
                                     else LexicalCandidateIndex())
                        for event_id in event_ids:
                            index_obj.add(EventAnchor(
                                event_id=event_id, scope_id=scope,
                                event_type="unknown",
                                canonical_title=event_texts[event_id][-1][:80],
                                anchor_text=" ".join(event_texts[event_id])))
                        for candidate in index_obj.search(scope, message["text"],
                                                          k=CANDIDATE_TOP_K):
                            candidates.append({
                                "event_id": candidate.event_id,
                                "event_type": "unknown",
                                "title": event_texts[candidate.event_id][-1][:80],
                                "anchor_text": " ".join(event_texts[candidate.event_id]),
                            })
                    if candidates:
                        judgment = judge.judge(scope, source, candidates,
                                               context=recent_context[-2:])
                        if gate is not None:
                            decision = gate.evaluate(GateInput(
                                scope_id=scope, source_id=message["source_id"],
                                source_text=message["text"], candidates=candidates,
                                relation_proposals=judgment.proposals,
                                context_text=" ".join(s.text for s in recent_context[-2:])))
                            stats["gate_" + decision.action.lower()] += 1
                            if decision.action != "ALLOW":
                                for proposal in judgment.proposals:
                                    if proposal.label == "SAME_EVENT":
                                        proposal.label = "UNCERTAIN"
                        summary = resolver.resolve(scope, source, judgment,
                                                   model_version=model)
                        stats["attached"] += summary.attached
                        if summary.conflicts:
                            stats["invalid"] += 1
                        attached = store.events_for_source(scope, index)
                        if not attached:
                            event_id = store.create_event_from_source(
                                scope, source, event_type="unknown",
                                title=message["text"][:80])
                            event_ids.append(event_id)
                            attached = [event_id]
                            stats["created"] += 1
                    else:
                        event_id = store.create_event_from_source(
                            scope, source, event_type="unknown",
                            title=message["text"][:80])
                        event_ids.append(event_id)
                        attached = [event_id]
                        stats["created"] += 1
                    predictions[message["source_id"]] = attached[0]
                    trace.append({
                        "scope_id": scope,
                        "source_id": message["source_id"],
                        "source_seq": index,
                        "candidates": [c["event_id"] for c in candidates],
                        "proposals": [{"candidate_event_id": p.candidate_event_id,
                                       "label": p.label} for p in
                                      (judgment.proposals if candidates else [])],
                        "issues": (judgment.issues if candidates else []),
                        "final_event": attached[0],
                    })
                    event_texts[attached[0]].append(message["text"])
                    recent_context.append(source)
                    stats["messages"] += 1
                    if stats["messages"] % batch_size == 0:
                        stats["batches"] += 1
        finally:
            store.close()
    return {"suite": "v1.5b-b4-r12-cluster", "model": model,
            "scopes": len(scopes), **stats,
            "predictions": predictions, "trace": trace}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--max-scopes", type=int, default=0)
    parser.add_argument("--bge-model", default="")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = run(args.root, args.api_key_file, args.model, args.max_scopes,
                 args.bge_model, args.batch_size, args.gate)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # Materialise cluster predictions for the fixture evaluator.
    pred_path = out.with_suffix(".jsonl")
    with pred_path.open("w", encoding="utf-8") as fh:
        for source_id, event_id in sorted(result["predictions"].items()):
            fh.write(json.dumps({"source_id": source_id,
                                 "predicted_event_id": event_id},
                                ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("scopes", "messages", "attached",
                                              "created", "invalid", "gate_allow",
                                              "gate_abstain", "gate_reject")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
