"""B4 retrieval baseline on the synthetic adversarial fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from b4_fixture import B4Fixture
from v1_5_consolidator.candidate_retriever import (EventAnchor,
                                                   HybridCandidateIndex,
                                                   LexicalCandidateIndex)


def evaluate(root: str, encoder=None) -> dict:
    fixture = B4Fixture.load(root)
    ks = (1, 5, 10)
    hits = {k: 0 for k in ks}
    total = 0
    reciprocal = 0.0
    abstain_expected = 0
    abstain_injected = {1: 0, 5: 0}
    errors = []
    for query in fixture.retrieval_queries:
        gold = fixture.retrieval_gold[query["query_id"]]
        query_message = fixture.messages[query["source_id"]]
        scope = query["scope_id"]
        index = HybridCandidateIndex(encoder) if encoder is not None else LexicalCandidateIndex()
        event_ids = []
        for event in fixture.events.values():
            if event["scope_id"] != scope:
                continue
            prior = fixture.event_sources_before(event["event_id"], query["source_id"])
            if not prior:
                continue
            text = " ".join(item["text"] for item in prior)
            index.add(EventAnchor(event_id=event["event_id"], scope_id=scope,
                                  event_type=event.get("event_type", "unknown"),
                                  canonical_title=prior[-1]["text"][:80],
                                  anchor_text=text,
                                  evidence_refs=[item["source_id"] for item in prior]))
            event_ids.append(event["event_id"])
        ranked = [item.event_id for item in index.search(scope, query_message["text"], k=max(ks))]
        if gold["expected"] != "ATTACH_EXISTING":
            abstain_expected += 1
            for k in (1, 5):
                if ranked[:k]:
                    abstain_injected[k] += 1
            continue
        total += 1
        if gold["gold_event_id"] in ranked:
            reciprocal += 1.0 / (ranked.index(gold["gold_event_id"]) + 1)
        for k in ks:
            if gold["gold_event_id"] in ranked[:k]:
                hits[k] += 1
        if gold["gold_event_id"] not in ranked[:max(ks)]:
            errors.append({"query_id": query["query_id"], "gold_event": gold["gold_event_id"],
                           "ranked": ranked[:5]})
    return {"scope_count": len({q["scope_id"] for q in fixture.retrieval_queries}),
            "queries_attach": total, "abstain_expected": abstain_expected,
            "abstain_false_injection": {
                str(k): round(abstain_injected[k] / abstain_expected, 4)
                if abstain_expected else 0.0 for k in (1, 5)},
            "recall": {str(k): round(hits[k] / total, 4) if total else 0.0 for k in ks},
            "mrr": round(reciprocal / total, 4) if total else 0.0,
            "errors": errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--bge-model", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    encoder = None
    if args.bge_model:
        from sentence_transformers import SentenceTransformer
        encoder = SentenceTransformer(args.bge_model, device="cpu")
    result = evaluate(args.root, encoder)
    payload = {"suite": "v1.5b-b4-retrieval",
               "method": "bm25+bge" if encoder is not None else "lexical",
               "dataset": "synthetic_b4_v1", **result}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("method", "scope_count", "queries_attach",
                                              "abstain_expected", "recall", "mrr")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
