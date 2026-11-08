"""B3 constrained resolver: proposals to append-only, reversible decisions.

Rules:
- SAME_EVENT is only a proposal. It attaches only when no existing source
  membership conflicts with a cannot-link relation.
- DIFFERENT_EVENT creates a cannot-link between the target and any event that
  already contains this source.
- RELATED_CONTEXT never attaches.
- UNCERTAIN / invalid labels defer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import SourceEnvelope
from .relation_judge import JudgmentResult
from .store import V15Store


@dataclass
class ResolutionSummary:
    attached: int = 0
    already_attached: int = 0
    deferred: int = 0
    related: int = 0
    cannot_link: int = 0
    conflicts: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)


class ConstrainedResolver:
    def __init__(self, store: V15Store):
        self.store = store

    def resolve(self, scope_id: str, source: SourceEnvelope,
                judgment: JudgmentResult, model_version: str = "") -> ResolutionSummary:
        summary = ResolutionSummary()
        # Fail closed on partially parsed or mismatched model judgments.
        # Never persist partial proposals, attach evidence, or add cannot-links
        # when the judge reported any validation issue.
        if not judgment.ok or judgment.source_seq != source.source_seq:
            issues = list(judgment.issues)
            if judgment.source_seq != source.source_seq:
                issues.append("source_seq_mismatch")
            if not judgment.proposals:
                issues.append("no_valid_proposals")
            for proposal in judgment.proposals or [None]:
                summary.deferred += 1
                summary.decisions.append({
                    "source_seq": source.source_seq,
                    "target": proposal.candidate_event_id if proposal else None,
                    "decision": "DEFERRED",
                    "reason": "invalid_judgment",
                    "issues": list(issues),
                })
            return summary
        existing = set(self.store.events_for_source(scope_id, source.source_seq))
        same_props = [p for p in judgment.proposals if p.label == "SAME_EVENT"]
        ambiguous_same = len(same_props) > 1
        for proposal in judgment.proposals:
            proposal_id = self.store.record_link_proposal(
                scope_id, source.source_seq, proposal.candidate_event_id,
                proposal.label, proposal.reason_codes, model_version, judgment.request_id)
            target = proposal.candidate_event_id
            if proposal.label == "SAME_EVENT":
                if not self.store.event_in_scope(target, scope_id):
                    self.store.record_link_decision(
                        proposal_id, scope_id, source.source_seq, target, "CONFLICT",
                        "cross_scope_or_missing_event")
                    summary.conflicts += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target, "decision": "CONFLICT"})
                    continue
                if ambiguous_same:
                    self.store.record_link_decision(
                        proposal_id, scope_id, source.source_seq, target, "CONFLICT",
                        "multiple_same_events")
                    summary.conflicts += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target, "decision": "CONFLICT"})
                    continue
                conflicts = [event_id for event_id in existing
                             if self.store.cannot_link_exists(scope_id, event_id, target)]
                if conflicts:
                    self.store.record_link_decision(
                        proposal_id, scope_id, source.source_seq, target, "CONFLICT",
                        "cannot_link:" + ",".join(sorted(conflicts)))
                    summary.conflicts += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target, "decision": "CONFLICT"})
                    continue
                inserted = self.store.attach_source(scope_id, source, target, proposal_id)
                if inserted:
                    summary.attached += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target, "decision": "ATTACHED"})
                else:
                    summary.already_attached += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target, "decision": "ALREADY_ATTACHED"})
            elif proposal.label == "DIFFERENT_EVENT":
                for event_id in existing:
                    self.store.add_cannot_link(scope_id, event_id, target, source.source_seq)
                self.store.record_link_decision(
                    proposal_id, scope_id, source.source_seq, target, "CANNOT_LINK",
                    "different_event")
                summary.cannot_link += 1
                summary.decisions.append({"source_seq": source.source_seq,
                                          "target": target, "decision": "CANNOT_LINK"})
            elif proposal.label == "RELATED_CONTEXT":
                self.store.record_link_decision(
                    proposal_id, scope_id, source.source_seq, target, "RELATED_ONLY",
                    "related_context")
                summary.related += 1
                summary.decisions.append({"source_seq": source.source_seq,
                                          "target": target, "decision": "RELATED_ONLY"})
            else:
                self.store.record_link_decision(
                    proposal_id, scope_id, source.source_seq, target, "DEFERRED",
                    "uncertain_or_invalid")
                summary.deferred += 1
                summary.decisions.append({"source_seq": source.source_seq,
                                          "target": target, "decision": "DEFERRED"})
        return summary
