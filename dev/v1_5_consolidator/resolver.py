"""B3 constrained resolver: proposals to append-only, reversible decisions.

Rules:
- SAME_EVENT is only a proposal. It attaches only when no existing source
  membership conflicts with a cannot-link relation.
- DIFFERENT_EVENT creates a cannot-link between the target and every event that
  contains this source, including events this same call just attached.
- RELATED_CONTEXT never attaches.
- UNCERTAIN / invalid labels defer.

The resolver decides memberships first and persists exclusions afterwards, both
inside one transaction, so the order of the judge's proposals cannot change the
stored relations.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from .contracts import NarrativeProvenance, SourceEnvelope
from .provenance_guard import ProvenanceGuard
from .relation_judge import JudgmentResult, RelationProposal
from .store import V15Store


@dataclass
class ResolutionSummary:
    attached: int = 0
    already_attached: int = 0
    deferred: int = 0
    related: int = 0
    # Number of DIFFERENT_EVENT proposals that actually persisted at least one
    # cannot-link relation. Proposals with nothing to bind are reported in
    # ``decisions`` but are not counted as a persisted exclusion.
    cannot_link: int = 0
    conflicts: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)


class ConstrainedResolver:
    def __init__(self, store: V15Store, provenance_guard: ProvenanceGuard | None = None):
        self.store = store
        # Guard cannot be silently disabled for a provenance-bearing judgment.
        self.provenance_guard = provenance_guard or ProvenanceGuard()

    def _audit_gate(self, *args, **kwargs):
        method = getattr(self.store, "record_gate_audit", None)
        if method is not None:
            method(*args, **kwargs)

    def _transaction(self):
        """One transaction for memberships and cannot-links when the store has one."""
        unit = getattr(self.store, "unit_of_work", None)
        return unit() if callable(unit) else nullcontext()

    def _effective(self, proposal: RelationProposal,
                   judgment: JudgmentResult) -> RelationProposal:
        """同一故事内的 DIFFERENT_EVENT 归一化为 RELATED_CONTEXT。"""
        if (proposal.label == "DIFFERENT_EVENT"
                and judgment.provenance_present
                and judgment.provenance.subject == "story_character"
                and judgment.provenance.story_id):
            candidate_data = self.store.event_provenance(
                proposal.candidate_event_id)
            candidate = NarrativeProvenance(**candidate_data) if candidate_data else None
            if candidate and candidate.story_id == judgment.provenance.story_id:
                reasons = list(proposal.reason_codes)
                if "same_story_different_event" not in reasons:
                    reasons.append("same_story_different_event")
                return RelationProposal(
                    candidate_event_id=proposal.candidate_event_id,
                    label="RELATED_CONTEXT",
                    reason_codes=reasons,
                    raw_label=proposal.label,
                    raw_reason_codes=list(proposal.reason_codes),
                    normalization_reason="same_story_different_event",
                    policy_version="same-story-related-v1")
        return proposal

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
        membership = set(self.store.events_for_source(scope_id, source.source_seq))
        same_props = [p for p in judgment.proposals if p.label == "SAME_EVENT"]
        ambiguous_same = len(same_props) > 1
        with self._transaction():
            # Record every proposal first, in judge order, exactly as parsed.
            recorded = []
            for raw in judgment.proposals:
                proposal = self._effective(raw, judgment)
                proposal_id = self.store.record_link_proposal(
                    scope_id, source.source_seq, proposal.candidate_event_id,
                    proposal.label, proposal.reason_codes, model_version,
                    judgment.request_id,
                    raw_label=proposal.raw_label,
                    raw_reason_codes=proposal.raw_reason_codes,
                    normalization_reason=proposal.normalization_reason,
                    policy_version=proposal.policy_version)
                recorded.append((proposal, proposal_id))
            # Phase 1: decide this source's memberships. Exclusions must see the
            # final membership, not the snapshot taken before the loop.
            for proposal, proposal_id in recorded:
                if proposal.label != "SAME_EVENT":
                    continue
                target = proposal.candidate_event_id
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
                gate_action = "NOT_APPLIED"
                gate_reasons = ["provenance_missing"]
                gate_policy = "provenance-not-applied"
                if (self.provenance_guard is not None
                        and judgment.provenance_present):
                    candidate_data = self.store.event_provenance(target)
                    check = self.provenance_guard.check(
                        judgment.provenance,
                        NarrativeProvenance(**candidate_data))
                    gate_action = check.action
                    gate_reasons = list(check.reason_codes)
                    gate_policy = check.policy_version
                    if check.action != "ALLOW":
                        self._audit_gate(
                            judgment.request_id, scope_id, source.source_seq,
                            "ProvenanceGuard", gate_action, gate_reasons,
                            gate_policy, target, proposal.label,
                            "PROVENANCE_BLOCKED")
                        self.store.record_link_decision(
                            proposal_id, scope_id, source.source_seq, target,
                            "PROVENANCE_BLOCKED", ",".join(check.reason_codes))
                        summary.deferred += 1
                        summary.decisions.append({"source_seq": source.source_seq,
                                                  "target": target,
                                                  "decision": "PROVENANCE_BLOCKED"})
                        continue
                conflicts = [event_id for event_id in membership
                             if self.store.cannot_link_exists(scope_id, event_id, target)]
                if conflicts:
                    if self.provenance_guard is not None:
                        self._audit_gate(
                            judgment.request_id, scope_id, source.source_seq,
                            "ProvenanceGuard", gate_action, gate_reasons,
                            gate_policy, target, proposal.label, "CONFLICT")
                    self.store.record_link_decision(
                        proposal_id, scope_id, source.source_seq, target, "CONFLICT",
                        "cannot_link:" + ",".join(sorted(conflicts)))
                    summary.conflicts += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target, "decision": "CONFLICT"})
                    continue
                inserted = self.store.attach_source(scope_id, source, target, proposal_id)
                membership.add(target)
                decision = "ATTACHED" if inserted else "ALREADY_ATTACHED"
                if inserted:
                    summary.attached += 1
                else:
                    summary.already_attached += 1
                summary.decisions.append({"source_seq": source.source_seq,
                                          "target": target, "decision": decision})
                if self.provenance_guard is not None:
                    self._audit_gate(
                        judgment.request_id, scope_id, source.source_seq,
                        "ProvenanceGuard", gate_action, gate_reasons,
                        gate_policy, target, proposal.label, decision)
            # Phase 2: persist exclusions against the final membership.
            for proposal, proposal_id in recorded:
                target = proposal.candidate_event_id
                if proposal.label == "DIFFERENT_EVENT":
                    if target in membership:
                        # 原话已归属 target，却又声明与它不同：矛盾，不能静默成功。
                        self.store.record_link_decision(
                            proposal_id, scope_id, source.source_seq, target,
                            "CONFLICT", "different_from_attached_event")
                        summary.conflicts += 1
                        summary.decisions.append({"source_seq": source.source_seq,
                                                  "target": target,
                                                  "decision": "CONFLICT"})
                        continue
                    linked = []
                    for event_id in sorted(membership):
                        if self.store.cannot_link_exists(scope_id, event_id, target):
                            continue
                        self.store.add_cannot_link(scope_id, event_id, target,
                                                   source.source_seq)
                        linked.append(event_id)
                    reason = "different_event"
                    if linked:
                        reason += ":linked=" + ",".join(linked)
                    self.store.record_link_decision(
                        proposal_id, scope_id, source.source_seq, target,
                        "CANNOT_LINK", reason)
                    if linked:
                        summary.cannot_link += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target,
                                              "decision": "CANNOT_LINK",
                                              "linked": len(linked)})
                elif proposal.label == "RELATED_CONTEXT":
                    self.store.record_link_decision(
                        proposal_id, scope_id, source.source_seq, target, "RELATED_ONLY",
                        "related_context")
                    summary.related += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target,
                                              "decision": "RELATED_ONLY"})
                elif proposal.label != "SAME_EVENT":
                    self.store.record_link_decision(
                        proposal_id, scope_id, source.source_seq, target, "DEFERRED",
                        "uncertain_or_invalid")
                    summary.deferred += 1
                    summary.decisions.append({"source_seq": source.source_seq,
                                              "target": target, "decision": "DEFERRED"})
        return summary
