"""B4.4-2 experimental EvidenceGate.

Default-off. The gate only inspects structured proposals and candidate anchors;
it never reads V1, calls a model, or writes an event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .relation_judge import RelationProposal

WEAK_REFERENCE = ("那个", "那件事", "上次", "后来呢", "这件事", "这个", "它")
DATE_RE = re.compile(r"(?:[0-9]{1,2}|十|十一|十二)月(?:[0-9]{1,2}日)?|今天|明天|昨天|下周|上周|下个月|上个月")


@dataclass(frozen=True)
class GateInput:
    scope_id: str
    source_id: str
    source_text: str
    candidates: list[dict]
    relation_proposals: list[RelationProposal]
    context_text: str = ""


@dataclass(frozen=True)
class GateDecision:
    action: str
    target_event_id: str | None
    reason_codes: tuple[str, ...]
    supporting_source_ids: tuple[str, ...] = field(default_factory=tuple)
    policy_version: str = "b4.4-gate-1"


def _bigrams(text: str) -> set[str]:
    chars = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


class EvidenceGate:
    def __init__(self, min_overlap: int = 1):
        self.min_overlap = int(min_overlap)

    def evaluate(self, gate_input: GateInput) -> GateDecision:
        same = [p for p in gate_input.relation_proposals if p.label == "SAME_EVENT"]
        if not same:
            return GateDecision("ABSTAIN", None, ("no_same_proposal",))
        if len(same) > 1:
            return GateDecision("ABSTAIN", None, ("multiple_same_targets",))
        target = same[0].candidate_event_id
        candidate = next((c for c in gate_input.candidates
                          if c.get("event_id") == target), None)
        if candidate is None:
            return GateDecision("REJECT", None, ("candidate_missing",))
        text = gate_input.source_text or ""
        anchor = (candidate.get("anchor_text") or "") + " " + (candidate.get("title") or "")
        support = []
        if _bigrams(text) & _bigrams(anchor):
            support.append(str(candidate.get("event_id", "")))
        if not support:
            if any(cue in text for cue in WEAK_REFERENCE):
                return GateDecision("ABSTAIN", None, ("weak_reference_without_anchor",))
            return GateDecision("ABSTAIN", None, ("no_lexical_anchor",))
        source_dates = set(DATE_RE.findall(text))
        anchor_dates = set(DATE_RE.findall(anchor))
        if source_dates and anchor_dates and source_dates.isdisjoint(anchor_dates):
            return GateDecision("REJECT", None, ("explicit_date_conflict",))
        return GateDecision("ALLOW", target, ("lexical_anchor",), tuple(support))
