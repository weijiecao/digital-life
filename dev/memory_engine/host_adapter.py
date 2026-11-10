"""Thin simulated host adapter for M7 contract tests.

It only translates a host-shaped event into SourceEnvelope and returns the
engine's ContextPacket. It does not call AstrBot or a reply model.
"""

from __future__ import annotations

from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope


class SimHostAdapter:
    def __init__(self, engine: MemoryEngine):
        self.engine = engine

    def ingest_event(self, event: dict) -> dict:
        required = ("scope_id", "source_seq", "text")
        missing = [key for key in required if key not in event]
        if missing:
            return {"ok": False, "error": "missing:" + ",".join(missing)}
        source = SourceEnvelope(
            event["scope_id"], int(event["source_seq"]),
            str(event.get("hash", "host")), str(event.get("recorded_at", "host")),
            str(event["text"]))
        receipt = self.engine.ingest(event["scope_id"], source,
                                     event.get("provenance"))
        return {"ok": receipt.receipt_ok, "event_id": receipt.event_id}

    def handle_message(self, event: dict, query: str | None = None,
                       budget: int = 2000) -> dict:
        ingested = self.ingest_event(event)
        packet = self.engine.build_context(
            event["scope_id"], query or event["text"], budget)
        return {"ingest": ingested, "packet": packet}
