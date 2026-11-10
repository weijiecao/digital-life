"""M4 threshold split: choose lexical min_score on dev, report holdout."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from b4_fixture import B4Fixture
from v1_5_consolidator.candidate_retriever import EventAnchor, LexicalCandidateIndex

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "b4_testdata" / "Digital_Life_B4_TestData_v1"


def collect():
    fixture = B4Fixture.load(str(FIXTURE))
    rows = []
    for query in fixture.retrieval_queries:
        gold = fixture.retrieval_gold[query["query_id"]]
        message = fixture.messages[query["source_id"]]
        index = LexicalCandidateIndex()
        for event in fixture.events.values():
            if event["scope_id"] != query["scope_id"]:
                continue
            prior = fixture.event_sources_before(event["event_id"], query["source_id"])
            if not prior:
                continue
            index.add(EventAnchor(
                event_id=event["event_id"], scope_id=query["scope_id"],
                canonical_title=prior[-1]["text"][:80],
                anchor_text=" ".join(item["text"] for item in prior)))
        ranked = index.search(query["scope_id"], message["text"], k=10)
        scores = {c.event_id: c.score for c in ranked}
        rows.append({"query_id": query["query_id"],
                     "attach": gold["expected"] == "ATTACH_EXISTING",
                     "gold": gold.get("gold_event_id"),
                     "scores": scores,
                     "dev": int(hashlib.sha256(
                         query["query_id"].encode()).hexdigest(), 16) % 2 == 0})
    return rows


def metrics(rows, threshold):
    attach = [r for r in rows if r["attach"]]
    abstain = [r for r in rows if not r["attach"]]
    hit = sum(1 for r in attach if r["scores"].get(r["gold"], 0) >= threshold)
    inj = sum(1 for r in abstain if any(s >= threshold for s in r["scores"].values()))
    return {"attach": len(attach), "hit": hit,
            "recall": round(hit / len(attach), 4) if attach else 0.0,
            "abstain": len(abstain), "injected": inj,
            "injection_rate": round(inj / len(abstain), 4) if abstain else 0.0}


def main() -> int:
    rows = collect()
    dev = [r for r in rows if r["dev"]]
    holdout = [r for r in rows if not r["dev"]]
    scores = sorted({score for row in dev for score in row["scores"].values()})
    candidates = [0.0] + scores
    ranked = [(t, metrics(dev, t)) for t in candidates]
    safe = [(t, m) for t, m in ranked if m["injected"] == 0]
    chosen = max(safe, key=lambda item: (item[1]["recall"], item[0])) if safe else (0.0, metrics(dev, 0.0))
    payload = {"suite": "m4-threshold-split", "dev_queries": len(dev),
               "holdout_queries": len(holdout),
               "chosen_threshold": chosen[0],
               "dev": chosen[1], "holdout": metrics(holdout, chosen[0]),
               "dev_curve": [{"threshold": t, **m} for t, m in ranked]}
    print(json.dumps({k: payload[k] for k in ("chosen_threshold", "dev", "holdout")},
                     ensure_ascii=False, indent=2))
    (ROOT.parent / "reports" / "m4_threshold_split.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
