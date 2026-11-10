"""Cluster-level quality on the 340 provenance trace against B4 fixture gold."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "b4_testdata" / "Digital_Life_B4_TestData_v1"


def evaluate(pred_path: Path) -> dict:
    gold_rows = [json.loads(line) for line in
                 (FIXTURE / "message_gold.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    gold = {row["source_id"]: row["gold_event_id"]
            for row in gold_rows if row.get("gold_event_id")}
    rows = [json.loads(line) for line in pred_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    clusters = defaultdict(set)
    for row in rows:
        gid = gold.get(row["source_id"])
        if gid:
            clusters[row["predicted_event_id"]].add(gid)
    mixed = sum(1 for ids in clusters.values() if len(ids) > 1)
    pure = sum(1 for ids in clusters.values() if len(ids) == 1)
    covered = sum(len(ids) for ids in clusters.values())
    return {"predicted_clusters": len(clusters), "pure_clusters": pure,
            "mixed_clusters": mixed,
            "mixed_cluster_rate": round(mixed / max(1, len(clusters)), 4),
            "gold_covered": covered,
            "total_gold_sources": len(gold)}


def main() -> int:
    output = {}
    for policy in ("abstain", "allow_user_real"):
        path = ROOT.parent / "reports" / ("v1_5b_b4_prov_replay_%s.jsonl" % policy)
        output[policy] = evaluate(path)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    (ROOT.parent / "reports" / "m2_eval_340.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
