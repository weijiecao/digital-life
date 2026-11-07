"""B2 gold-candidate relation-judge evaluation.

Each query message is given a fixed candidate set containing its gold event
and three negatives. The LLM only labels relations; no event is written.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.relation_judge import RelationJudge
from v1_5_consolidator.store import V15Store


def messages_for_scope(db_path: str, scope: str) -> dict[int, tuple[str, str]]:
    db = sqlite3.connect("file:" + str(Path(db_path).resolve()) + "?mode=ro", uri=True)
    rows = db.execute("SELECT seq,content,content_sha,recorded_at FROM dl_capture WHERE scope=?",
                      (scope,)).fetchall()
    db.close()
    return {int(seq): (content, sha, recorded) for seq, content, sha, recorded in rows}


def evaluate_scope(db_path: str, scope: str, groups: list[dict], judge: RelationJudge) -> dict:
    rows = messages_for_scope(db_path, scope)
    anchors = []
    for group in groups:
        first = group["seqs"][0]
        content = rows[first][0]
        event_type = {"trip_oct": "plan", "trip_dec": "plan",
                      "museum": "experience", "habit": "state_change"}.get(
                          group["id"], "unknown")
        anchors.append({"event_id": group["id"], "event_type": event_type,
                        "title": content[:80], "anchor_text": content,
                        "evidence_refs": [first]})
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "abstain": 0, "invalid": 0}
    label_counts: dict[str, int] = {}
    for group in groups:
        for query_index, query_seq in enumerate(group["seqs"][1:], start=1):
            content, sha, recorded = rows[query_seq]
            source = SourceEnvelope(scope, query_seq, sha, recorded, content)
            # Gold local context for the isolation experiment: only same-event
            # messages before the query, never the query or future messages.
            context = [SourceEnvelope(scope, seq, rows[seq][1], rows[seq][2], rows[seq][0])
                       for seq in group["seqs"][:query_index]]
            result = judge.judge(scope, source, anchors, context=context)
            if result.issues:
                counts["invalid"] += 1
            proposals = {item.candidate_event_id: item.label for item in result.proposals}
            for anchor in anchors:
                gold_same = anchor["event_id"] == group["id"]
                label = proposals.get(anchor["event_id"])
                if label is None:
                    counts["invalid"] += 1
                    continue
                label_counts[label] = label_counts.get(label, 0) + 1
                if label == "UNCERTAIN":
                    counts["abstain"] += 1
                predicted_same = label == "SAME_EVENT"
                if predicted_same and gold_same:
                    counts["tp"] += 1
                elif predicted_same and not gold_same:
                    counts["fp"] += 1
                elif not predicted_same and gold_same:
                    counts["fn"] += 1
                else:
                    counts["tn"] += 1
    return {"scope": scope, **counts, "labels": label_counts}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-db", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--base-url", default="https://api.deepseek.com/v1")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--max-scopes", type=int, default=0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
    provider = OpenAIProvider(args.base_url, args.model, key)
    results = []
    scopes = labels["scopes"][:args.max_scopes] if args.max_scopes else labels["scopes"]
    for scope in scopes:
        with tempfile.TemporaryDirectory(prefix="v15-b2-") as tmp:
            store = V15Store(str(Path(tmp) / "v15.sqlite3"))
            gateway = ContextGateway(store, deny_external=False)
            judge = RelationJudge(gateway, provider)
            try:
                result = evaluate_scope(args.v1_db, scope["scope"], scope["groups"], judge)
            finally:
                store.close()
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    keys = ("tp", "fp", "fn", "tn", "abstain", "invalid")
    totals = {key: sum(item[key] for item in results) for key in keys}
    precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 0.0
    recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    labels_total: dict[str, int] = {}
    for item in results:
        for label, count in item["labels"].items():
            labels_total[label] = labels_total.get(label, 0) + count
    payload = {"suite": "v1.5b-relation-judge-gold-candidates",
               "model": args.model, "scopes": len(results),
               "pairs": sum(totals.values()) - totals["abstain"] - totals["invalid"],
               "tp": totals["tp"], "fp": totals["fp"],
               "fn": totals["fn"], "tn": totals["tn"],
               "abstain": totals["abstain"], "invalid": totals["invalid"],
               "precision": round(precision, 4), "recall": round(recall, 4),
               "f1": round(f1, 4), "labels": labels_total, "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "scopes", "pairs", "precision", "recall", "f1", "abstain", "invalid", "labels")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
