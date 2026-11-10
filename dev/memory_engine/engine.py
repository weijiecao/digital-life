"""Minimal independent offline text engine façade.

This is the first M3 slice: source ingest, lexical retrieval, context packet and
source deletion on top of V15Store. It intentionally does not add model calls or
host integration.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store

from memory_engine.cache import ContextCache
from memory_engine.projection import classify_memory
from memory_engine.summary_cache import SummaryCache
from v1_5_consolidator.candidate_retriever import EventAnchor, LexicalCandidateIndex


@dataclass(frozen=True)
class CaptureReceipt:
    scope_id: str
    source_seq: int
    event_id: str
    receipt_ok: bool


class MemoryEngine:
    def __init__(self, store: V15Store, cache: ContextCache | None = None,
                 summary_cache: SummaryCache | None = None):
        self.store = store
        self.cache = cache or ContextCache()
        self.summary_cache = summary_cache or SummaryCache()
        self._generation = defaultdict(int)

    def _bump(self, scope_id: str) -> None:
        self._generation[scope_id] += 1
        self.cache.invalidate_scope(scope_id)

    def ingest(self, scope_id: str, source: SourceEnvelope,
               provenance: dict | None = None) -> CaptureReceipt:
        existing = self.store.receipt_for_source(scope_id, source.source_seq)
        if existing is not None:
            events = self.store.events_for_source(scope_id, source.source_seq)
            return CaptureReceipt(scope_id, source.source_seq,
                                  events[0] if events else "",
                                  existing["input_hash"] == source.source_hash)
        with self.store.unit_of_work():
            event_id = self.store.create_event_from_source(
                scope_id, source, event_type="unknown",
                title=source.text[:80], provenance=provenance)
            item_type, modality = classify_memory(source.text)
            self.store.save_memory_item(scope_id, event_id, item_type,
                                        source.text, modality)
            self.store.attach_source(scope_id, source, event_id, "engine-ingest")
            receipt_ok = self.store.record_receipt(
                scope_id, source.source_seq, "ATTACHED", source.source_hash)
        self.store.bump_scope_epoch(scope_id)
        self._bump(scope_id)
        return CaptureReceipt(scope_id, source.source_seq, event_id, receipt_ok)

    def retrieve(self, scope_id: str, query: str, limit: int = 10,
                 min_score: float = 0.0) -> list[dict]:
        pattern = "%" + query.replace("%", "").replace("_", "") + "%"
        rows = self.store.db.execute("""SELECT DISTINCT c.event_id,c.title,c.event_type,
            r.summary,e.quote
            FROM event_candidate c
            JOIN event_revision r ON r.event_id=c.event_id AND r.rev=c.current_revision
            JOIN event_evidence e ON e.event_id=c.event_id AND e.rev=c.current_revision
            WHERE c.scope_id=? AND c.state!='withdrawn' AND e.valid=1
            AND NOT EXISTS (SELECT 1 FROM resolution_pending p
                WHERE p.scope_id=c.scope_id AND p.source_seq=e.source_seq
                AND p.status='PENDING')
            AND (c.title LIKE ? OR r.summary LIKE ? OR e.quote LIKE ?)
            ORDER BY c.updated_at DESC LIMIT ?""",
            (scope_id, pattern, pattern, pattern, max(limit, 100))).fetchall()
        if min_score > 0 and query:
            index = LexicalCandidateIndex()
            by_id = {}
            for row in rows:
                by_id[row[0]] = row
                index.add(EventAnchor(
                    event_id=row[0], scope_id=scope_id,
                    canonical_title=row[1] or "",
                    anchor_text=" ".join(str(part or "") for part in
                                         (row[1], row[3], row[4]))))
            ranked = index.search(scope_id, query, k=limit, min_score=min_score)
            rows = [by_id[c.event_id] for c in ranked if c.event_id in by_id]
        else:
            rows = rows[:limit]
        return [{"event_id": row[0], "title": row[1], "event_type": row[2],
                 "summary": self.summary_cache.get(row[3] or row[1] or ""),
                 "evidence": row[4]} for row in rows]

    def build_context(self, scope_id: str, query: str, budget: int = 2000,
                      budget_tokens: int | None = None,
                      token_counter=None, min_score: float = 0.0) -> dict:
        if budget_tokens is not None and token_counter is None:
            budget = min(budget, max(0, budget_tokens) * 4)
        tokenizer_name = getattr(token_counter, "encoding_name", None)
        db_version = self.store.db.execute("PRAGMA data_version").fetchone()[0]
        epoch = self.store.scope_epoch(scope_id)
        key = (scope_id, query, budget, budget_tokens, tokenizer_name,
               db_version, epoch, min_score)
        generation = self._generation[scope_id]
        cached = self.cache.get(key, generation)
        if cached is not None:
            return cached
        items = self.retrieve(scope_id, query, limit=10, min_score=min_score)
        selected = []
        used = 0
        used_tokens = 0
        for item in items:
            chars = len(item.get("summary", "")) + len(item.get("evidence", ""))
            tokens = (token_counter.count(item.get("summary", ""))
                      + token_counter.count(item.get("evidence", ""))
                      if token_counter is not None else None)
            if token_counter is not None and budget_tokens is not None:
                if used_tokens + tokens > budget_tokens:
                    break
                used_tokens += tokens
            elif used + chars > budget:
                break
            selected.append(item)
            used += chars
        packet = {"schema_version": "memory-context-v1", "scope_id": scope_id,
                  "query": query, "items": selected, "source_refs": [
                      {"event_id": item["event_id"]} for item in selected],
                  "budget_chars": budget, "used_chars": used,
                  "budget_tokens": budget_tokens,
                  "used_tokens": (used_tokens if token_counter is not None
                                  else ((used + 3) // 4 if budget_tokens is not None else None)),
                  "tokenizer": tokenizer_name,
                  "degraded": len(selected) < len(items)}
        if token_counter is not None and budget_tokens is not None:
            from memory_engine.render import fit_packet
            packet = fit_packet(packet, token_counter, budget_tokens)
        self.cache.put(key, generation, packet)
        return packet

    def delete_source(self, scope_id: str, source_seq: int) -> int:
        count = self.store.delete_source(scope_id, source_seq)
        self._bump(scope_id)
        return count

    def revise_source(self, scope_id: str, old_source_seq: int,
                      new_source: SourceEnvelope,
                      provenance: dict | None = None) -> str:
        """Replace a source's current membership with a new event."""
        identity = self.store.revision_identity(
            scope_id, old_source_seq, new_source.source_seq)
        if identity and identity["input_hash"] != new_source.source_hash:
            raise ValueError("revision identity hash conflict")
        existing = self.store.revision_result(
            scope_id, old_source_seq, new_source.source_seq, new_source.source_hash)
        if existing:
            return existing
        with self.store.unit_of_work():
            identity = self.store.revision_identity(
                scope_id, old_source_seq, new_source.source_seq)
            if identity and identity["input_hash"] != new_source.source_hash:
                raise ValueError("revision identity hash conflict")
            existing = self.store.revision_result(
                scope_id, old_source_seq, new_source.source_seq, new_source.source_hash)
            if existing:
                return existing
            old_events = self.store.events_for_source(scope_id, old_source_seq)
            self.store.invalidate_source_evidence(scope_id, old_source_seq)
            event_id = self.store.create_event_from_source(
                scope_id, new_source, event_type="unknown",
                title=new_source.text[:80], provenance=provenance)
            self.store.attach_source(scope_id, new_source, event_id, "revise")
            self.store.record_receipt(scope_id, new_source.source_seq,
                                      "REVISED", new_source.source_hash)
            for old_event in old_events:
                self.store.db.execute("""INSERT INTO event_relation
                    (scope_id,from_event,to_event,relation_type,status,source_seq,created_at)
                    VALUES(?,?,?,'SUPERSEDES','active',?,?)""",
                    (scope_id, old_event, event_id, new_source.source_seq,
                     "2026-11-10T00:00:00+00:00"))
            self.store.save_revision_result(
                scope_id, old_source_seq, new_source.source_seq,
                new_source.source_hash, event_id)
        self.store.bump_scope_epoch(scope_id)
        self._bump(scope_id)
        return event_id

    def withdraw_event(self, scope_id: str, event_id: str) -> bool:
        ok = self.store.withdraw_event(scope_id, event_id)
        self._bump(scope_id)
        return ok

    def build_rendered_context(self, scope_id: str, query: str,
                               budget_tokens: int, token_counter=None,
                               min_score: float = 0.0) -> dict:
        """Render the final model block and enforce its token budget."""
        from memory_engine.render import fit_packet, render_context
        packet = self.build_context(scope_id, query, budget=10 ** 9,
                                    min_score=min_score)
        if token_counter is None:
            rendered = render_context(packet)
            packet = dict(packet)
            packet.update({"rendered": rendered, "rendered_tokens": None,
                           "budget_enforced": False, "estimated": True})
            return packet
        packet = fit_packet(packet, token_counter, budget_tokens)
        packet["estimated"] = False
        return packet

    def health(self) -> dict:
        return {"events": self.store.db.execute(
            "SELECT COUNT(*) FROM event_candidate").fetchone()[0],
            "pending": self.store.db.execute(
                "SELECT COUNT(*) FROM resolution_pending WHERE status='PENDING'").fetchone()[0]}
