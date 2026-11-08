"""R11-7A/7F gold-v2 evaluation: story membership and event identity separated."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
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
    story_total = story_correct = 0
    event_total = event_correct = 0
    contamination = 0
    unknown_story = 0
    labels = Counter()
    results = []
    with tempfile.TemporaryDirectory(prefix="r11-v2-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        judge = RelationJudge(ContextGateway(store, deny_external=False), provider)
        try:
            for case in cases:
                source = SourceEnvelope("r11", 1, "sha", "2026-11-08", case["story_text"])
                candidate_prov = NarrativeProvenance(**case["candidate_provenance"])
                candidate = {"event_id": "story-event-1", "event_type": "unknown",
                             "title": case["candidate_text"][:80],
                             "anchor_text": case["candidate_text"],
                             "provenance": case["candidate_provenance"]}
                result = judge.judge("r11", source, [candidate])
                predicted = next((p.label for p in result.proposals
                                  if p.candidate_event_id == "story-event-1"), None)
                decision = guard.check(result.provenance, candidate_prov)
                labels[predicted] += 1
                # Story membership is evaluated separately from event identity.
                if case["same_story"] == "UNKNOWN":
                    unknown_story += 1
                    membership_ok = None
                else:
                    story_total += 1
                    observed = (result.provenance.story_id is not None
                                and result.provenance.story_id == candidate_prov.story_id)
                    membership_ok = (observed if case["same_story"] == "YES" else not observed)
                    story_correct += int(membership_ok)
                event_total += 1
                event_ok = predicted == case["expected_relation"]
                event_correct += int(event_ok)
                # Contamination means narrative/third-party content attached to
                # a *user real* event; same-story/same-event is legitimate.
                if (candidate_prov.subject == "user"
                        and candidate_prov.reality_status == "real"
                        and result.provenance.subject != "user"
                        and predicted == "SAME_EVENT"
                        and decision.action == "ALLOW"):
                    contamination += 1
                results.append({"case_id": case["case_id"], "category": case["category"],
                                "predicted": predicted,
                                "expected_relation": case["expected_relation"],
                                "story_membership_ok": membership_ok,
                                "event_relation_ok": event_ok,
                                "guard_action": decision.action,
                                "source_story_id": result.provenance.story_id,
                                "candidate_story_id": candidate_prov.story_id})
        finally:
            store.close()
    payload = {"suite": "v1.5b-b4-r11-gold-v2", "cases": len(cases),
               "story_membership": {"scored": story_total, "correct": story_correct,
                                    "unknown": unknown_story},
               "event_relation": {"scored": event_total, "correct": event_correct},
               "real_event_contamination": contamination,
               "predicted_labels": dict(labels), "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("cases", "story_membership",
                                              "event_relation", "real_event_contamination",
                                              "predicted_labels")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
