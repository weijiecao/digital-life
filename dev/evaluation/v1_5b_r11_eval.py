"""Evaluate synthetic R11 A-F cases against the current relation judge."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.gateway import ContextGateway, OpenAIProvider
from v1_5_consolidator.provenance_guard import ProvenanceGuard
from v1_5_consolidator.relation_judge import RelationJudge
from v1_5_consolidator.store import V15Store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    provider = OpenAIProvider("https://api.deepseek.com/v1", args.model,
                              Path(args.api_key_file).read_text(encoding="utf-8").strip())
    labels = Counter()
    guard = ProvenanceGuard()
    guard_actions = Counter()
    effective_false_same = 0
    by_category = defaultdict(lambda: Counter({"false_same": 0, "blocked": 0, "total": 0}))
    results = []
    with tempfile.TemporaryDirectory(prefix="r11-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        judge = RelationJudge(ContextGateway(store, deny_external=False), provider)
        try:
            for row in rows:
                source = SourceEnvelope("r11", 1, "sha", "2026-11-08", row["story_text"])
                candidate = {
                    "event_id": "user-event",
                    "event_type": "experience",
                    "title": row["user_event_text"][:80],
                    "anchor_text": row["user_event_text"],
                }
                result = judge.judge("r11", source, [candidate])
                predicted = next((p.label for p in result.proposals
                                  if p.candidate_event_id == "user-event"), None)
                labels[predicted] += 1
                decision = guard.check(result.provenance,
                                       NarrativeProvenance("user", "user", "real"))
                guard_actions[decision.action] += 1
                by_category[row["category"]]["total"] += 1
                if predicted == "SAME_EVENT":
                    by_category[row["category"]]["false_same"] += 1
                    if decision.action == "ALLOW":
                        effective_false_same += 1
                    else:
                        by_category[row["category"]]["blocked"] += 1
                results.append({"case_id": row["case_id"], "category": row["category"],
                                "predicted": predicted,
                                "provenance": result.provenance.as_dict(),
                                "guard_action": decision.action,
                                "guard_reasons": list(decision.reason_codes),
                                "issues": result.issues})
        finally:
            store.close()
    payload = {"suite": "v1.5b-b4-r11-af", "cases": len(rows),
               "predicted_labels": dict(labels),
               "false_same": labels.get("SAME_EVENT", 0),
               "effective_false_same": effective_false_same,
               "guard_actions": dict(guard_actions),
               "by_category": {key: dict(value) for key, value in by_category.items()},
               "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "cases", "predicted_labels", "false_same", "effective_false_same",
        "guard_actions", "by_category")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
