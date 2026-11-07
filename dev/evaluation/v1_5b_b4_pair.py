"""B4 pair-level relation baseline on the synthetic adversarial fixture.

One gold candidate event per pair; this isolates relation judging from B1
retrieval. The fixture is synthetic, so results are diagnostic, not release
acceptance.
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
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.relation_judge import RelationJudge
from v1_5_consolidator.store import V15Store


def select_pairs(fixture: B4Fixture, per_class: int) -> list[dict]:
    grouped = defaultdict(list)
    for pair in fixture.pair_gold.values():
        grouped[pair["relation"]].append(pair)
    selected = []
    for relation in ("SAME_EVENT", "RELATED", "DIFFERENT_EVENT", "UNRESOLVED"):
        selected.extend(grouped[relation][:per_class])
    return selected


def evaluate(root: str, api_key_file: str, per_class: int, model: str) -> dict:
    fixture = B4Fixture.load(root)
    provider = OpenAIProvider("https://api.deepseek.com/v1", model,
                              Path(api_key_file).read_text(encoding="utf-8").strip())
    pairs = select_pairs(fixture, per_class)
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "invalid": 0}
    labels = defaultdict(int)
    errors = []
    with tempfile.TemporaryDirectory(prefix="b4-pair-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        gateway = ContextGateway(store, deny_external=False)
        judge = RelationJudge(gateway, provider)
        try:
            for pair in pairs:
                left = fixture.messages[pair["left_source_id"]]
                right = fixture.messages[pair["right_source_id"]]
                if left["scope_id"] != right["scope_id"]:
                    continue
                candidate_id = fixture.event_by_source.get(pair["right_source_id"])
                if not candidate_id:
                    continue
                query = left
                candidate_event = fixture.events[candidate_id]
                anchor_text = " ".join(fixture.messages[s]["text"]
                                       for s in candidate_event["source_ids"]
                                       if s in fixture.messages)
                context_messages = []
                left_event = fixture.event_by_source.get(pair["left_source_id"], "")
                if left_event:
                    context_messages = fixture.event_sources_before(
                        left_event, pair["left_source_id"])
                context = [SourceEnvelope(left["scope_id"], index, m["sha256_text"],
                                          m["recorded_at"], m["text"])
                           for index, m in enumerate(context_messages, start=1)]
                source = SourceEnvelope(left["scope_id"], 1, left["sha256_text"],
                                        left["recorded_at"], left["text"])
                result = judge.judge(left["scope_id"], source, [{
                    "event_id": candidate_id,
                    "event_type": candidate_event.get("event_type", "unknown"),
                    "title": candidate_event.get("event_desc", "")[:80],
                    "anchor_text": anchor_text,
                }], context=context)
                predicted = next((item.label for item in result.proposals
                                  if item.candidate_event_id == candidate_id), None)
                if predicted is None:
                    counts["invalid"] += 1
                    continue
                labels[predicted] += 1
                gold_same = pair["relation"] == "SAME_EVENT"
                predicted_same = predicted == "SAME_EVENT"
                if gold_same and predicted_same:
                    counts["tp"] += 1
                elif not gold_same and predicted_same:
                    counts["fp"] += 1
                elif gold_same and not predicted_same:
                    counts["fn"] += 1
                else:
                    counts["tn"] += 1
                    if predicted == "RELATED_CONTEXT":
                        pass
                if gold_same != predicted_same and len(errors) < 20:
                    errors.append({"pair_id": pair["pair_id"],
                                   "gold": pair["relation"], "predicted": predicted,
                                   "difficulty": pair.get("difficulty", "")})
        finally:
            store.close()
    precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
    recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"suite": "v1.5b-b4-pair", "model": model, "pairs": len(pairs),
            **counts, "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "labels": dict(labels), "errors": errors}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--per-class", type=int, default=50)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = evaluate(args.root, args.api_key_file, args.per_class, args.model)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("pairs", "tp", "fp", "fn", "tn",
                                             "precision", "recall", "f1", "labels")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
