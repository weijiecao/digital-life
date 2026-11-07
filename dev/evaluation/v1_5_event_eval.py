"""Evaluate V1.5 LLM event partitioning against a small human-labeled set.

The labels are a small real-message seed set, not a release benchmark. The
script reports pairwise precision/recall/F1 and false-merge rate; it does not
send the raw messages anywhere except to the configured LLM provider.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.engine import Consolidator
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.store import V15Store


def evaluate_round(v1_db: str, scope: str, labels: dict, provider) -> dict:
    with tempfile.TemporaryDirectory(prefix="v15-eval-") as tmp:
        v15 = str(Path(tmp) / "v15.sqlite3")
        store = V15Store(v15)
        gateway = ContextGateway(store, deny_external=False)
        consolidator = Consolidator(v1_db, v15, provider, gateway=gateway,
                                    min_messages=1, batch_limit=40, store=store)
        try:
            summary = consolidator.run_once(scope=scope, force=True)
        finally:
            consolidator.close()
        db = sqlite3.connect(v15)
        rows = db.execute("""SELECT source_seq,event_id FROM event_evidence
            WHERE valid=1 ORDER BY source_seq""").fetchall()
        db.close()
    mapping = {int(seq): event_id for seq, event_id in rows}
    groups = labels["groups"]
    allowed = sorted({seq for group in groups for seq in group["seqs"]})
    gold_pairs = {tuple(sorted(pair)) for group in groups
                  for pair in combinations(group["seqs"], 2)}
    predicted = {seq: mapping.get(seq) for seq in allowed}
    pred_pairs = {tuple(sorted(pair)) for pair in combinations(allowed, 2)
                  if predicted[pair[0]] and predicted[pair[0]] == predicted[pair[1]]}
    tp = len(gold_pairs & pred_pairs)
    precision = tp / len(pred_pairs) if pred_pairs else 0.0
    recall = tp / len(gold_pairs) if gold_pairs else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    false_merges = len(pred_pairs - gold_pairs)
    false_merge_rate = false_merges / len(gold_pairs) if gold_pairs else 0.0
    coverage = sum(1 for seq in allowed if predicted[seq]) / len(allowed) if allowed else 0.0
    return {"round": None, "events_created": summary.events_created,
            "rejected": summary.rejected, "coverage": round(coverage, 4),
            "pair_precision": round(precision, 4), "pair_recall": round(recall, 4),
            "pair_f1": round(f1, 4), "false_merge_rate": round(false_merge_rate, 4),
            "gold_pairs": len(gold_pairs), "pred_pairs": len(pred_pairs),
            "tp": tp, "false_merges": false_merges}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-db", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--base-url", default="https://api.deepseek.com/v1")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
    provider = OpenAIProvider(args.base_url, args.model, key)
    results = []
    for round_index in range(1, args.rounds + 1):
        result = evaluate_round(args.v1_db, labels["scope"], labels, provider)
        result["round"] = round_index
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))
    means = {}
    for key_name in ("coverage", "pair_precision", "pair_recall", "pair_f1",
                     "false_merge_rate"):
        means[key_name] = round(sum(item[key_name] for item in results) / len(results), 4)
    payload = {"suite": "v1.5-event-partition-eval", "date": datetime.now(timezone.utc).isoformat(),
               "model": args.model, "rounds": args.rounds,
               "labels": str(args.labels), "results": results, "means": means}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"means": means}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
