"""Minimal independent offline text engine façade.

This is the first M3 slice: source ingest, lexical retrieval, context packet and
source deletion on top of V15Store. It intentionally does not add model calls or
host integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


@dataclass(frozen=True)
class CaptureReceipt:
    scope_id: str
    source_seq: int
    event_id: str
    receipt_ok: bool


class MemoryEngine:
    def __init__(self, store: V15Store):
        self.store = store

    def ingest(self, scope_id: str, source: SourceEnvelope,
               provenance: dict | None = None) -> CaptureReceipt:
        with self.store.unit_of_work():
            event_id = self.store.create_event_from_source(
                scope_id, source, event_type="unknown",
                title=source.text[:80], provenance=provenance)
            self.store.attach_source(scope_id, source, event_id, "engine-ingest")
            receipt_ok = self.store.record_receipt(
                scope_id, source.source_seq, "ATTACHED", source.source_hash)
        return CaptureReceipt(scope_id, source.source_seq, event_id, receipt_ok)

    def retrieve(self, scope_id: str, query: str, limit: int = 10) -> list[dict]:
        pattern = "%" + query.replace("%", "").replace("_", "") + "%"
        rows = self.store.db.execute("""SELECT DISTINCT c.event_id,c.title,c.event_type,
            r.summary,e.quote
            FROM event_candidate c
            JOIN event_revision r ON r.event_id=c.event_id AND r.rev=c.current_revision
            JOIN event_evidence e ON e.event_id=c.event_id AND e.rev=c.current_revision
            WHERE c.scope_id=? AND c.state!='withdrawn' AND e.valid=1
            AND (c.title LIKE ? OR r.summary LIKE ? OR e.quote LIKE ?)
            ORDER BY c.updated_at DESC LIMIT ?""",
            (scope_id, pattern, pattern, pattern, limit)).fetchall()
        return [{"event_id": row[0], "title": row[1], "event_type": row[2],
                 "summary": row[3], "evidence": row[4]} for row in rows]

    def build_context(self, scope_id: str, query: str, budget: int = 2000) -> dict:
        items = self.retrieve(scope_id, query, limit=10)
        selected = []
        used = 0
        for item in items:
            size = len(item.get("summary", "")) + len(item.get("evidence", ""))
            if used + size > budget:
                break
            selected.append(item)
            used += size
        return {"schema_version": "memory-context-v1", "scope_id": scope_id,
                "query": query, "items": selected, "source_refs": [
                    {"event_id": item["event_id"]} for item in selected],
                "budget_chars": budget, "used_chars": used,
                "degraded": len(selected) < len(items)}

    def delete_source(self, scope_id: str, source_seq: int) -> int:
        return self.store.delete_source(scope_id, source_seq)

    def health(self) -> dict:
        return {"events": self.store.db.execute(
            "SELECT COUNT(*) FROM event_candidate").fetchone()[0],
            "pending": self.store.db.execute(
                "SELECT COUNT(*) FROM resolution_pending WHERE status='PENDING'").fetchone()[0]}
