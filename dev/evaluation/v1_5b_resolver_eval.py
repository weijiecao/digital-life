"""B3.1 replay: feed saved B2 relation predictions into ConstrainedResolver.

This measures attachment precision/recall without calling the LLM again.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


def rows_for_scope(db_path: str, scope: str) -> dict[int, tuple[str, str, str]]:
    db = sqlite3.connect("file:" + str(Path(db_path).resolve()) + "?mode=ro", uri=True)
    rows = db.execute("SELECT seq,content,content_sha,recorded_at FROM dl_capture WHERE scope=?",
                      (scope,)).fetchall()
    db.close()
    return {int(seq): (content, sha, recorded) for seq, content, sha, recorded in rows}


def seed_event(store: V15Store, event_id: str, scope: str, title: str) -> None:
    now = "2026-11-07T10:00:00+00:00"
    store.db.execute("""INSERT OR IGNORE INTO event_candidate
        (event_id,scope_id,event_type,title,state,current_revision,created_at,updated_at)
        VALUES(?,?,?,?,'provisional',1,?,?)""",
        (event_id, scope, "plan", title[:80], now, now))
    store.db.execute("""INSERT OR IGNORE INTO event_revision
        (event_id,rev,summary,status,modality,created_at) VALUES(?,1,?,?,?,?)""",
        (event_id, title[:200], "unknown", "unknown", now))
    store.db.commit()


def evaluate_scope(v1_db: str, scope: str, groups: list[dict],
                   predictions: list[dict]) -> dict:
    rows = rows_for_scope(v1_db, scope)
    with tempfile.TemporaryDirectory(prefix="v15-b3-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        resolver = ConstrainedResolver(store)
        for group in groups:
            first = group["seqs"][0]
            content, sha, recorded = rows[first]
            seed_event(store, group["id"], scope, content)
            store.attach_source(scope, SourceEnvelope(scope, first, sha, recorded, content),
                                group["id"], "seed_" + group["id"])
        counts = {"queries": 0, "attached_correct": 0, "attached_wrong": 0,
                  "not_attached": 0, "conflicts": 0}
        for prediction in sorted(predictions, key=lambda item: item["query_seq"]):
            query_seq = int(prediction["query_seq"])
            content, sha, recorded = rows[query_seq]
            proposals = [RelationProposal(candidate_id, label)
                         for candidate_id, label in prediction["relations"].items()]
            judgment = JudgmentResult(source_seq=query_seq, proposals=proposals,
                                      request_id="b2_" + str(query_seq))
            summary = resolver.resolve(scope, SourceEnvelope(scope, query_seq, sha, recorded, content),
                                       judgment, model_version="b2-replay")
            counts["conflicts"] += summary.conflicts
            attached = set(store.events_for_source(scope, query_seq))
            gold = prediction["gold_event"]
            counts["queries"] += 1
            if gold in attached and attached == {gold}:
                counts["attached_correct"] += 1
            elif attached:
                counts["attached_wrong"] += 1
            else:
                counts["not_attached"] += 1
        store.close()
    return {"scope": scope, **counts}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-db", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    pred_payload = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    by_scope: dict[str, list[dict]] = defaultdict(list)
    for prediction in pred_payload["predictions"]:
        by_scope[prediction["scope"]].append(prediction)
    results = [evaluate_scope(args.v1_db, scope["scope"], scope["groups"],
                              by_scope[scope["scope"]])
               for scope in labels["scopes"]]
    queries = sum(item["queries"] for item in results)
    correct = sum(item["attached_correct"] for item in results)
    wrong = sum(item["attached_wrong"] for item in results)
    attached = correct + wrong
    payload = {"suite": "v1.5b-b3-resolver-replay",
               "queries": queries, "attached_correct": correct,
               "attached_wrong": wrong, "not_attached": sum(item["not_attached"] for item in results),
               "conflicts": sum(item["conflicts"] for item in results),
               "attachment_precision": round(correct / attached, 4) if attached else 0.0,
               "attachment_recall": round(correct / queries, 4) if queries else 0.0,
               "false_merge_rate": round(wrong / queries, 4) if queries else 0.0,
               "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "queries", "attached_correct", "attached_wrong", "not_attached",
        "conflicts", "attachment_precision", "attachment_recall", "false_merge_rate")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
