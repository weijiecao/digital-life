"""Consolidation orchestration and deterministic validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .adapter import V1ReadOnlyAdapter
from .contracts import (ACTIONS, EVENT_TYPES, MODALITIES, STATUS,
                        ProposalOperation, SourceEnvelope, ValidationIssue,
                        ValidationResult)
from .gateway import ContextGateway, ModelResult, PreparedContext
from .store import V15Store


def _digest(sources: list[SourceEnvelope]) -> str:
    payload = [{"scope": s.scope_id, "seq": s.source_seq, "sha": s.source_hash}
               for s in sources]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("operations"), list):
        raise ValueError("proposal 必须是含 operations 列表的 JSON 对象")
    return data


class ProposalValidator:
    def __init__(self, scope_id: str, sources: list[SourceEnvelope],
                 candidates: list[dict[str, Any]]):
        self.scope_id = scope_id
        self.sources = {s.source_seq: s for s in sources}
        self.candidate_ids = {c["event_id"] for c in candidates}

    def validate(self, proposal: dict[str, Any]) -> ValidationResult:
        result = ValidationResult()
        for raw in proposal.get("operations", []):
            issues = []
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", ""))
            event_type = str(raw.get("event_type", ""))
            modality = str(raw.get("modality", "unknown"))
            status = str(raw.get("status_proposal", "unknown"))
            if action not in ACTIONS:
                issues.append(ValidationIssue("action", "非法 action"))
            if event_type not in EVENT_TYPES:
                issues.append(ValidationIssue("event_type", "非法 event_type"))
            if modality not in MODALITIES:
                issues.append(ValidationIssue("modality", "非法 modality"))
            if status not in STATUS:
                issues.append(ValidationIssue("status", "非法 status"))
            refs = raw.get("source_refs")
            if not isinstance(refs, list) or not refs:
                issues.append(ValidationIssue("source_refs", "缺少来源"))
                refs = []
            valid_refs = []
            for ref in refs:
                if not isinstance(ref, dict):
                    issues.append(ValidationIssue("source_ref", "来源不是对象"))
                    continue
                seq = ref.get("source_seq")
                quote = str(ref.get("quote", ""))
                if type(seq) is not int or seq not in self.sources:
                    issues.append(ValidationIssue("source_seq", "来源不在当前批次"))
                    continue
                if not quote or quote not in self.sources[seq].text:
                    issues.append(ValidationIssue("quote", "引用不是原文子串"))
                    continue
                valid_refs.append({"source_seq": seq, "quote": quote})
            candidate_id = raw.get("candidate_event_id")
            if action in ("update", "link"):
                if candidate_id not in self.candidate_ids:
                    issues.append(ValidationIssue("candidate_event_id", "候选事件不在允许集合"))
            elif action == "create" and candidate_id:
                issues.append(ValidationIssue("candidate_event_id", "create 不应带旧事件 ID"))
            title = str(raw.get("title", "")).strip()
            summary = str(raw.get("summary", "")).strip()
            if action == "create" and not (title or summary):
                issues.append(ValidationIssue("title", "create 缺少标题/摘要"))
            op = ProposalOperation(
                action=action, event_type=event_type, source_refs=valid_refs,
                title=title, summary=summary, modality=modality,
                status_proposal=status, candidate_event_id=candidate_id,
                occurred_at=raw.get("occurred_at"),
                time_expression_raw=raw.get("time_expression_raw"),
                uncertainties=list(raw.get("uncertainties") or []))
            if issues:
                result.rejected.append((op, issues))
            else:
                result.accepted.append(op)
        return result


@dataclass
class RunSummary:
    scopes: int = 0
    batches: int = 0
    events_created: int = 0
    rejected: int = 0
    tombstones: int = 0
    skipped: int = 0


class Consolidator:
    def __init__(self, v1_db: str, v15_db: str, provider, gateway: ContextGateway | None = None,
                 min_messages: int = 20, batch_limit: int = 40, store: V15Store | None = None):
        self.adapter = V1ReadOnlyAdapter(v1_db)
        self.store = store or V15Store(v15_db)
        self.provider = provider
        self.gateway = gateway or ContextGateway(self.store)
        self.min_messages = min_messages
        self.batch_limit = batch_limit

    def close(self) -> None:
        self.adapter.close()
        self.store.close()

    def run_once(self, scope: str | None = None, force: bool = False) -> RunSummary:
        summary = RunSummary()
        run_id = "scan_" + hashlib.sha256(str(id(self)).encode()).hexdigest()[:12]
        summary.tombstones = self.store.sync_inventory(self.adapter.source_keys(), run_id)
        scopes = [scope] if scope else self.adapter.scopes()
        for scope_id in scopes:
            summary.scopes += 1
            cursor = self.store.cursor(scope_id)
            sources = self.adapter.read_after(scope_id, cursor, self.batch_limit)
            if not sources or (len(sources) < self.min_messages and not force):
                summary.skipped += 1
                continue
            digest = _digest(sources)
            job_id = self.store.record_job(scope_id, sources[0].source_seq,
                                           sources[-1].source_seq, digest, "running")
            if self.store.job_status(job_id) == "done":
                summary.skipped += 1
                continue
            summary.batches += 1
            candidates = self.store.candidates(scope_id)
            try:
                prepared = self.gateway.prepare(scope_id, sources, candidates)
                if getattr(self.provider, "external", False) and self.gateway.deny_external:
                    raise RuntimeError("ContextGateway 禁止外发；请显式允许外部 provider")
                result = self.provider.generate(prepared)
                proposal = _extract_json(result.text)
                validation = ProposalValidator(scope_id, sources, candidates).validate(proposal)
                summary.rejected += len(validation.rejected)
                if validation.rejected:
                    self.store.record_validation_issues(job_id, validation.rejected)
                if validation.accepted:
                    created = self.store.commit(
                        scope_id, sources, validation.accepted, job_id,
                        model_version=result.model, prompt_version=self.gateway.policy_version,
                        input_digest=digest)
                    summary.events_created += created
                else:
                    self.store.finish_job(job_id, "deferred")
                self.store.record_usage(job_id, result.model or "unknown",
                                        result.tokens_in, result.tokens_out, "ok")
            except Exception as exc:
                self.store.finish_job(job_id, "failed")
                self.store.record_usage(job_id, "unknown", 0, 0, "error:" + type(exc).__name__)
                raise
        return summary
