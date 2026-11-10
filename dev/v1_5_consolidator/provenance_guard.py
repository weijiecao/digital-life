"""Minimal narrative provenance guard for B4.5-6.

The guard is a deterministic compatibility check between the source provenance
returned by the judge and the candidate event's stored provenance. It does not
call a model and does not mutate storage.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import NarrativeProvenance


@dataclass(frozen=True)
class ProvenanceDecision:
    action: str
    reason_codes: tuple[str, ...]
    policy_version: str = "b4.5-6-provenance-1"


class ProvenanceGuard:
    def __init__(self, unknown_candidate: str = "abstain"):
        if unknown_candidate not in ("abstain", "allow_user_real"):
            raise ValueError("unknown_candidate must be abstain or allow_user_real")
        self.unknown_candidate = unknown_candidate

    def check(self, source: NarrativeProvenance,
              candidate: NarrativeProvenance) -> ProvenanceDecision:
        source_narrative = (source.reality_status in ("fictional", "reported")
                            or source.subject != "user")
        candidate_user_real = (candidate.subject == "user"
                               and candidate.reality_status == "real")
        if source_narrative and candidate_user_real:
            return ProvenanceDecision("REJECT", ("narrative_to_user_event",))
        if source.reality_status == "unknown" or candidate.reality_status == "unknown":
            if (self.unknown_candidate == "allow_user_real"
                    and source.subject == "user"
                    and source.reality_status == "real"
                    and candidate.reality_status == "unknown"):
                return ProvenanceDecision("ALLOW", ("user_real_to_unknown",))
            return ProvenanceDecision("ABSTAIN", ("provenance_unknown",))
        if (source.reality_status in ("fictional", "reported")
                and candidate.reality_status in ("fictional", "reported")):
            if source.story_id and candidate.story_id and source.story_id != candidate.story_id:
                return ProvenanceDecision("REJECT", ("different_story_id",))
            return ProvenanceDecision("ALLOW", ("same_narrative_scope",))
        if source.subject == "user" and candidate.subject == "user":
            return ProvenanceDecision("ALLOW", ("user_real",))
        return ProvenanceDecision("ABSTAIN", ("provenance_incompatible",))
