"""R11-7B series evaluation for story continuity and Guard trade-offs."""

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
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    cases = [json.loads(line) for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    provider = OpenAIProvider("https://api.deepseek.com/v1", "deepseek-chat",
                              Path(args.api_key_file).read_text(encoding="utf-8").strip())
    guard = ProvenanceGuard()
    results = []
    story_scored = story_correct = 0
    event_scored = event_correct = 0
    contamination = false_reject = 0
    by_category = defaultdict(Counter)
    with tempfile.TemporaryDirectory(prefix="r11-series-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        judge = RelationJudge(ContextGateway(store, deny_external=False), provider)
        try:
            for idx, case in enumerate(cases, start=1):
                event_id = "series-event-%02d" % idx
                source = SourceEnvelope("r11", 1, "sha", "2026-11-08", case["story_text"])
                candidate_prov = NarrativeProvenance(**case["candidate_provenance"])
                candidate = {"event_id": event_id, "event_type": "unknown",
                             "title": case["candidate_text"][:80],
                             "anchor_text": case["candidate_text"],
                             "provenance": case["candidate_provenance"]}
                result = judge.judge("r11", source, [candidate])
                predicted = next((p.label for p in result.proposals
                                  if p.candidate_event_id == event_id), None)
                decision = guard.check(result.provenance, candidate_prov)
                event_scored += 1
                event_ok = predicted == case["expected_relation"]
                event_correct += int(event_ok)
                if case["same_story"] != "UNKNOWN":
                    story_scored += 1
                    same = (result.provenance.story_id is not None
                            and result.provenance.story_id == candidate_prov.story_id)
                    story_correct += int(same if case["same_story"] == "YES" else not same)
                if (case["category"] == "narrative_to_user"
                        and predicted == "SAME_EVENT" and decision.action == "ALLOW"):
                    contamination += 1
                if (case["expected_relation"] == "SAME_EVENT"
                        and predicted == "SAME_EVENT" and decision.action != "ALLOW"):
                    false_reject += 1
                by_category[case["category"]]["total"] += 1
                by_category[case["category"]]["event_ok"] += int(event_ok)
                by_category[case["category"]]["guard_" + decision.action.lower()] += 1
                results.append({"case_id": case["case_id"], "category": case["category"],
                                "predicted": predicted,
                                "expected_relation": case["expected_relation"],
                                "event_ok": event_ok,
                                "guard_action": decision.action,
                                "guard_reasons": list(decision.reason_codes),
                                "source_story_id": result.provenance.story_id,
                                "candidate_story_id": candidate_prov.story_id})
        finally:
            store.close()
    payload = {"suite": "v1.5b-b4-r11-series20", "cases": len(cases),
               "story_membership": {"scored": story_scored, "correct": story_correct},
               "event_relation": {"scored": event_scored, "correct": event_correct},
               "real_event_contamination": contamination,
               "guard_false_reject": false_reject,
               "by_category": {key: dict(value) for key, value in by_category.items()},
               "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "cases", "story_membership", "event_relation", "real_event_contamination",
        "guard_false_reject", "by_category")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
