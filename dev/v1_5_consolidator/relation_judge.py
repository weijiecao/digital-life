"""B2 relation judge: LLM proposes, deterministic parser validates.

The judge never writes an event or a membership decision. It only returns
candidate relations for later constrained resolution.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import NarrativeProvenance, SourceEnvelope
from .gateway import ContextGateway, PreparedRelationContext

RELATION_LABELS = {"SAME_EVENT", "DIFFERENT_EVENT", "RELATED_CONTEXT", "UNCERTAIN"}
SUGGESTED_ACTIONS = {"ATTACH_EXISTING", "NEW_EVENT", "DEFER"}
NARRATORS = {"user", "friend", "story_character", "unknown"}
SUBJECTS = {"user", "other", "story_character", "unknown"}
REALITY = {"real", "reported", "fictional", "unknown"}


@dataclass
class RelationProposal:
    candidate_event_id: str
    label: str
    reason_codes: list[str] = field(default_factory=list)


@dataclass
class JudgmentResult:
    source_seq: int
    proposals: list[RelationProposal] = field(default_factory=list)
    suggested_action: str = "DEFER"
    target_event_id: str | None = None
    issues: list[str] = field(default_factory=list)
    raw: str = ""
    request_id: str = ""
    provenance: NarrativeProvenance = field(default_factory=NarrativeProvenance)

    @property
    def ok(self) -> bool:
        return not self.issues and bool(self.proposals)


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("relations"), list):
        raise ValueError("relation 输出必须是含 relations 列表的 JSON")
    return data


class RelationJudge:
    def __init__(self, gateway: ContextGateway, provider):
        self.gateway = gateway
        self.provider = provider

    def judge(self, scope_id: str, source: SourceEnvelope,
              candidates: list[dict[str, Any]],
              context: list[SourceEnvelope] | None = None) -> JudgmentResult:
        prepared = self.gateway.prepare_relation(scope_id, source, candidates, context=context)
        if getattr(self.provider, "external", False) and self.gateway.deny_external:
            raise RuntimeError("ContextGateway 禁止外发；关系判别需要显式允许外部 provider")
        result = self.provider.generate(prepared)
        try:
            data = _extract_json(result.text)
        except Exception as exc:
            return JudgmentResult(source_seq=source.source_seq, issues=["json:" + type(exc).__name__],
                                  raw=result.text, request_id=prepared.request_id)
        allowed_ids = {str(item["event_id"]) for item in candidates}
        proposals: list[RelationProposal] = []
        issues: list[str] = []
        seen = set()
        for item in data.get("relations", []):
            if not isinstance(item, dict):
                issues.append("relation:not_object")
                continue
            candidate_id = str(item.get("candidate_event_id", ""))
            label = str(item.get("label", ""))
            if candidate_id not in allowed_ids:
                issues.append("candidate:" + candidate_id)
                continue
            if candidate_id in seen:
                issues.append("candidate_duplicate:" + candidate_id)
                continue
            if label not in RELATION_LABELS:
                issues.append("label:" + label)
                continue
            seen.add(candidate_id)
            proposals.append(RelationProposal(
                candidate_event_id=candidate_id, label=label,
                reason_codes=[str(x) for x in (item.get("reason_codes") or [])][:8]))
        missing = allowed_ids - seen
        if missing:
            issues.append("missing:" + ",".join(sorted(missing)))
        action = str(data.get("suggested_action", "DEFER"))
        if action not in SUGGESTED_ACTIONS:
            issues.append("action:" + action)
        target = data.get("target_event_id")
        provenance_data = data.get("source_provenance") or {}
        provenance = NarrativeProvenance(
            narrator=str(provenance_data.get("narrator", "unknown")),
            subject=str(provenance_data.get("subject", "unknown")),
            reality_status=str(provenance_data.get("reality_status", "unknown")),
            story_id=(str(provenance_data["story_id"])
                      if provenance_data.get("story_id") else None))
        if provenance.narrator not in NARRATORS:
            issues.append("provenance_narrator:" + provenance.narrator)
        if provenance.subject not in SUBJECTS:
            issues.append("provenance_subject:" + provenance.subject)
        if provenance.reality_status not in REALITY:
            issues.append("provenance_reality:" + provenance.reality_status)
        return JudgmentResult(source_seq=source.source_seq, proposals=proposals,
                              suggested_action=action,
                              target_event_id=str(target) if target else None,
                              issues=issues, raw=result.text,
                              request_id=prepared.request_id, provenance=provenance)
