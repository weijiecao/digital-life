"""Data contracts for the V1.5 event consolidation candidate layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EVENT_TYPES = {"plan", "experience", "state_change", "narrative"}
MODALITIES = {"completed", "planned", "possible", "quoted", "narrative", "unknown"}
ACTIONS = {"create", "update", "link", "defer"}
STATUS = {"planned", "possibly_postponed", "cancelled", "completed", "ongoing",
          "uncertain", "withdrawn", "unknown"}


@dataclass(frozen=True)
class SourceEnvelope:
    scope_id: str
    source_seq: int
    source_hash: str
    recorded_at: str
    text: str
    state: str = "pending"

    def ref(self) -> dict[str, Any]:
        return {"scope_id": self.scope_id, "source_seq": self.source_seq,
                "source_hash": self.source_hash}


@dataclass
class ProposalOperation:
    action: str
    event_type: str
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    summary: str = ""
    modality: str = "unknown"
    status_proposal: str = "unknown"
    candidate_event_id: str | None = None
    occurred_at: str | None = None
    time_expression_raw: str | None = None
    uncertainties: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ValidationIssue:
    code: str
    detail: str


@dataclass
class ValidationResult:
    accepted: list[ProposalOperation] = field(default_factory=list)
    rejected: list[tuple[ProposalOperation, list[ValidationIssue]]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.accepted) and not self.rejected
