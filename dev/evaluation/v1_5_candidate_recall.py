"""Candidate Recall@K evaluation for V1.5-B.

Given incremental event anchors (only messages already seen for each event),
retrieve the gold event for later mentions. No LLM is called.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.candidate_retriever import (EventAnchor,
                                                   HybridCandidateIndex,
                                                   LexicalCandidateIndex)


def load_messages(db_path: str, scope: str) -> dict[int, str]:
    db = sqlite3.connect("file:" + str(Path(db_path).resolve()) + "?mode=ro", uri=True)
    rows = db.execute("SELECT seq,content FROM dl_capture WHERE scope=?", (scope,)).fetchall()
    db.close()
    return {int(seq): text for seq, text in rows}


def evaluate_scope(db_path: str, scope: str, groups: list[dict], ks: tuple[int, ...],
                   encoder=None) -> dict:
    messages = load_messages(db_path, scope)
    hits = {k: 0 for k in ks}
    total = 0
    reciprocal = 0.0
    for group in groups:
        seqs = group["seqs"]
        for index in range(1, len(seqs)):
            query_seq = seqs[index]
            query = messages.get(query_seq, "")
            if not query:
                continue
            # Incremental anchors: each event can only use messages already seen
            # before this query; this prevents leaking the query itself.
            index_obj = HybridCandidateIndex(encoder) if encoder is not None else LexicalCandidateIndex()
            for other in groups:
                prefix = [s for s in other["seqs"] if s < query_seq]
                if not prefix:
                    continue
                index_obj.add(EventAnchor(
                    event_id=other["id"], scope_id=scope,
                    canonical_title=messages.get(prefix[0], "")[:80],
                    anchor_text=" ".join(messages.get(s, "") for s in prefix),
                    evidence_refs=prefix,
                ))
            candidates = index_obj.search(scope, query, k=max(ks))
            ranked = [c.event_id for c in candidates]
            total += 1
            if group["id"] in ranked:
                reciprocal += 1.0 / (ranked.index(group["id"]) + 1)
            for k in ks:
                if group["id"] in ranked[:k]:
                    hits[k] += 1
    return {"scope": scope, "queries": total,
            "recall": {str(k): round(hits[k] / total, 4) if total else 0.0 for k in ks},
            "mrr": round(reciprocal / total, 4) if total else 0.0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-db", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--bge-model", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    encoder = None
    if args.bge_model:
        from sentence_transformers import SentenceTransformer
        encoder = SentenceTransformer(args.bge_model, device="cpu")
    ks = (1, 3, 5, 10)
    results = [evaluate_scope(args.v1_db, scope["scope"], scope["groups"], ks, encoder)
               for scope in labels["scopes"]]
    queries = sum(item["queries"] for item in results)
    recall = {str(k): round(sum(item["recall"][str(k)] * item["queries"]
                                for item in results) / queries, 4) if queries else 0.0
              for k in ks}
    payload = {"suite": "v1.5-candidate-recall",
               "method": "bm25+bge" if encoder is not None else "lexical-bm25-char-bigram",
               "scopes": len(results), "queries": queries,
               "recall": recall,
               "mrr": round(sum(item["mrr"] * item["queries"] for item in results) / queries, 4)
               if queries else 0.0,
               "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("scopes", "queries", "recall", "mrr")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
