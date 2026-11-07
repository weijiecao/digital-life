"""Held-out evaluation for V1.5 event partitioning.

Runs the current prompt/validator on the generated held-out dataset. This is
not a release benchmark: it is a leakage check against the tuning examples.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.engine import Consolidator
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.store import V15Store


def evaluate_scope(v1_db: str, scope: str, groups: list[dict], provider,
                   batch_limit: int = 40) -> dict:
    with tempfile.TemporaryDirectory(prefix="v15-heldout-") as tmp:
        v15 = str(Path(tmp) / "v15.sqlite3")
        store = V15Store(v15)
        gateway = ContextGateway(store, deny_external=False)
        consolidator = Consolidator(v1_db, v15, provider, gateway=gateway,
                                    min_messages=1, batch_limit=batch_limit, store=store)
        try:
            summary = consolidator.run_once(scope=scope, force=True)
        finally:
            consolidator.close()
        db = sqlite3.connect(v15)
        rows = db.execute("""SELECT source_seq,event_id FROM event_evidence
            WHERE valid=1 ORDER BY source_seq""").fetchall()
        issues = [{"code": row[0], "detail": row[1]} for row in db.execute(
            "SELECT code,detail FROM validation_issue ORDER BY id")]
        db.close()
    mapping = {int(seq): event_id for seq, event_id in rows}
    allowed = sorted({seq for group in groups for seq in group["seqs"]})
    gold_pairs = {tuple(sorted(pair)) for group in groups
                  for pair in combinations(group["seqs"], 2)}
    predicted = {seq: mapping.get(seq) for seq in allowed}
    pred_pairs = {tuple(sorted(pair)) for pair in combinations(allowed, 2)
                  if predicted[pair[0]] and predicted[pair[0]] == predicted[pair[1]]}
    tp = len(gold_pairs & pred_pairs)
    coverage = sum(1 for seq in allowed if predicted[seq]) / len(allowed) if allowed else 0.0
    clusters: dict[str, list[int]] = {}
    for seq, event_id in mapping.items():
        if event_id and seq in allowed:
            clusters.setdefault(event_id, []).append(seq)
    return {"scope": scope, "events_created": summary.events_created,
            "rejected": summary.rejected, "gold_pairs": len(gold_pairs),
            "pred_pairs": len(pred_pairs), "tp": tp,
            "false_merges": len(pred_pairs - gold_pairs),
            "coverage": round(coverage, 4),
            "clusters": sorted((sorted(seqs) for seqs in clusters.values()),
                               key=lambda item: item[0]),
            "validation_issues": issues}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-db", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--base-url", default="https://api.deepseek.com/v1")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--batch-limit", type=int, default=40)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
    provider = OpenAIProvider(args.base_url, args.model, key)
    results = [evaluate_scope(args.v1_db, scope["scope"], scope["groups"], provider,
                              batch_limit=args.batch_limit)
               for scope in labels["scopes"]]
    tp = sum(item["tp"] for item in results)
    pred = sum(item["pred_pairs"] for item in results)
    gold = sum(item["gold_pairs"] for item in results)
    false_merges = sum(item["false_merges"] for item in results)
    coverage = sum(item["coverage"] for item in results) / len(results)
    precision = tp / pred if pred else 0.0
    recall = tp / gold if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    payload = {"suite": "v1.5-heldout-eval", "model": args.model,
               "batch_limit": args.batch_limit,
               "scopes": len(results), "gold_pairs": gold,
               "pred_pairs": pred, "tp": tp,
               "pair_precision": round(precision, 4),
               "pair_recall": round(recall, 4),
               "pair_f1": round(f1, 4),
               "false_merge_rate": round(false_merges / gold, 4) if gold else 0.0,
               "mean_source_coverage": round(coverage, 4),
               "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "scopes", "gold_pairs", "pred_pairs", "tp", "pair_precision",
        "pair_recall", "pair_f1", "false_merge_rate", "mean_source_coverage")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
