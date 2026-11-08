"""R11-S2 closed-loop replay: fixed source + fixed B2 judgment -> Guard -> Resolver.

This records pre-Guard hypothetical attachment vs post-Guard actual store writes.
It uses a temporary SQLite DB and does not call V1.
"""

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
from v1_5_consolidator.resolver import ConstrainedResolver
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
    trace = []
    pre_false_same = post_contamination = 0
    post_attached = post_deferred = 0
    with tempfile.TemporaryDirectory(prefix="r11-s2-") as tmp:
        store = V15Store(str(Path(tmp) / "v15.sqlite3"))
        judge = RelationJudge(ContextGateway(store, deny_external=False), provider)
        resolver = ConstrainedResolver(store)
        try:
            for idx, case in enumerate(cases, start=1):
                scope = "r11"
                event_seq = idx * 10
                event_id = "s2-event-%02d" % idx
                candidate_prov = NarrativeProvenance(**case["candidate_provenance"])
                candidate_source = SourceEnvelope(
                    scope, event_seq, "sha", "2026-11-08", case["candidate_text"])
                store.create_event_from_source(
                    scope, candidate_source, event_type="unknown",
                    title=case["candidate_text"][:80], event_id=event_id,
                    provenance=case["candidate_provenance"])
                source = SourceEnvelope(scope, event_seq + 1, "sha", "2026-11-08",
                                        case["story_text"])
                result = judge.judge(scope, source, [{
                    "event_id": event_id, "event_type": "unknown",
                    "title": case["candidate_text"][:80],
                    "anchor_text": case["candidate_text"],
                    "provenance": case["candidate_provenance"]}])
                predicted = next((p.label for p in result.proposals
                                  if p.candidate_event_id == event_id), None)
                decision = guard.check(result.provenance, candidate_prov)
                pre_same = predicted == "SAME_EVENT"
                if (pre_same and candidate_prov.subject == "user"
                        and candidate_prov.reality_status == "real"
                        and result.provenance.subject != "user"):
                    pre_false_same += 1
                summary = resolver.resolve(scope, source, result)
                attached = bool(store.events_for_source(scope, source.source_seq))
                if attached:
                    post_attached += 1
                else:
                    post_deferred += 1
                if (attached and candidate_prov.subject == "user"
                        and candidate_prov.reality_status == "real"
                        and result.provenance.subject != "user"):
                    post_contamination += 1
                store.record_gate_audit(
                    result.request_id, scope, source.source_seq, "ProvenanceGuard",
                    decision.action, list(decision.reason_codes), decision.policy_version,
                    event_id, predicted,
                    "ATTACHED" if attached else "DEFERRED")
                trace.append({
                    "case_id": case["case_id"], "source_id": source.source_seq,
                    "candidate_event_id": event_id,
                    "source_provenance": result.provenance.as_dict(),
                    "candidate_provenance": case["candidate_provenance"],
                    "model_label": predicted,
                    "guard_action": decision.action,
                    "guard_reasons": list(decision.reason_codes),
                    "pre_guard_same": pre_same,
                    "post_attached": attached,
                    "expected_relation": case["expected_relation"],
                })
        finally:
            store.close()
    payload = {"suite": "v1.5b-r11-s2-closed-loop", "cases": len(cases),
               "pre_guard_same": sum(1 for t in trace if t["pre_guard_same"]),
               "pre_guard_false_same": pre_false_same,
               "post_attached": post_attached,
               "post_deferred": post_deferred,
               "post_contamination": post_contamination,
               "trace": trace}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "cases", "pre_guard_same", "pre_guard_false_same",
        "post_attached", "post_deferred", "post_contamination")},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
