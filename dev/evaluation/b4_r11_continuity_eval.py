"""Evaluate ProvenanceGuard on same-story and user-real continuity cases."""

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
    by_category = defaultdict(Counter)
    with tempfile.TemporaryDirectory(prefix="r11-cont-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        judge = RelationJudge(ContextGateway(store, deny_external=False), provider)
        try:
            for case in cases:
                source = SourceEnvelope("r11", 1, "sha", "2026-11-08", case["story_text"])
                candidate = {"event_id": "candidate", "event_type": "unknown",
                             "title": case["candidate_text"][:80],
                             "anchor_text": case["candidate_text"],
                             "provenance": case["candidate_provenance"]}
                result = judge.judge("r11", source, [candidate])
                predicted = next((p.label for p in result.proposals
                                  if p.candidate_event_id == "candidate"), None)
                provenance = NarrativeProvenance(**case["candidate_provenance"])
                decision = guard.check(result.provenance, provenance)
                if case["expected"] == "SAME_EVENT":
                    ok = predicted == "SAME_EVENT" and decision.action == "ALLOW"
                    reason = ("ok" if ok else
                              "relation_miss" if predicted != "SAME_EVENT" else
                              "guard_false_reject")
                else:
                    ok = not (predicted == "SAME_EVENT" and decision.action == "ALLOW")
                    reason = ("ok" if ok else "guard_false_allow")
                by_category[case["category"]][reason] += 1
                results.append({"case_id": case["case_id"], "category": case["category"],
                                "predicted": predicted, "guard_action": decision.action,
                                "guard_reasons": list(decision.reason_codes),
                                "ok": ok, "reason": reason,
                                "source_provenance": result.provenance.as_dict()})
        finally:
            store.close()
    payload = {"suite": "v1.5b-b4-r11-continuity", "cases": len(cases),
               "passed": sum(1 for r in results if r["ok"]),
               "by_category": {k: dict(v) for k, v in by_category.items()},
               "results": results}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("cases", "passed", "by_category")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
