"""R11 narrative-vs-experience relation probe on the synthetic B4 fixture.

This is an isolation probe, not a narrative classifier: it asks the current
relation judge whether a narrative-bearing message is the same event as a
non-narrative event. Any SAME_EVENT is a potential misattribution.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from b4_fixture import B4Fixture
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.relation_judge import RelationJudge
from v1_5_consolidator.store import V15Store


def run(root: str, api_key_file: str, limit: int, model: str) -> dict:
    fixture = B4Fixture.load(root)
    provider = OpenAIProvider("https://api.deepseek.com/v1", model,
                              Path(api_key_file).read_text(encoding="utf-8").strip())
    event_type = {eid: event.get("event_type", "unknown")
                  for eid, event in fixture.events.items()}
    pairs = []
    for pair in fixture.pair_gold.values():
        left_event = fixture.event_by_source.get(pair["left_source_id"])
        right_event = fixture.event_by_source.get(pair["right_source_id"])
        if not left_event or not right_event:
            continue
        types = {event_type.get(left_event), event_type.get(right_event)}
        if "narrative" not in types:
            continue
        if types == {"narrative"}:
            continue
        pairs.append(pair)
        if len(pairs) >= limit:
            break
    results = []
    with tempfile.TemporaryDirectory(prefix="b4-r11-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        gateway = ContextGateway(store, deny_external=False)
        judge = RelationJudge(gateway, provider)
        try:
            for pair in pairs:
                left = fixture.messages[pair["left_source_id"]]
                right = fixture.messages[pair["right_source_id"]]
                right_event = fixture.event_by_source[pair["right_source_id"]]
                event = fixture.events[right_event]
                anchor_text = " ".join(fixture.messages[s]["text"]
                                       for s in event["source_ids"] if s in fixture.messages)
                source = SourceEnvelope(left["scope_id"], 1, left["sha256_text"],
                                        left["recorded_at"], left["text"])
                result = judge.judge(left["scope_id"], source, [{
                    "event_id": right_event,
                    "event_type": event_type.get(right_event, "unknown"),
                    "title": event.get("event_anchor", "")[:80],
                    "anchor_text": anchor_text,
                }])
                predicted = next((p.label for p in result.proposals
                                  if p.candidate_event_id == right_event), None)
                results.append({"pair_id": pair["pair_id"],
                                "gold": pair["relation"],
                                "left_type": event_type.get(fixture.event_by_source[
                                    pair["left_source_id"]]),
                                "right_type": event_type.get(right_event),
                                "predicted": predicted,
                                "issues": result.issues})
        finally:
            store.close()
    counts = {}
    for row in results:
        counts[row["predicted"]] = counts.get(row["predicted"], 0) + 1
    false_same = sum(1 for row in results if row["predicted"] == "SAME_EVENT")
    return {"suite": "v1.5b-b4-r11-narrative", "model": model,
            "pairs": len(results), "predicted_labels": counts,
            "same_event_predictions": false_same,
            "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = run(args.root, args.api_key_file, args.limit, args.model)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("pairs", "predicted_labels",
                                              "same_event_predictions")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
