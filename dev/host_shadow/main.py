"""AstrBot shadow plugin for the offline Memory Engine.

Install this directory under AstrBot's data/plugins and set:
  DIGITAL_LIFE_DEV=/path/to/digital-life/dev
  ASTRBOT_ROOT=/path/to/AstrBot/instance
It observes private text and on_llm_request, writes its own DB/log, and never
modifies the request or the reply.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from astrbot.api.event import filter
from astrbot.api.star import Context, Star

DEV = Path(os.environ.get("DIGITAL_LIFE_DEV", "")).resolve()
if not DEV.exists():
    raise RuntimeError("DIGITAL_LIFE_DEV must point to the digital-life/dev directory")
sys.path.insert(0, str(DEV))

from memory_engine.engine import MemoryEngine
from memory_engine.host_budget import HostBudget
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store

try:
    from memory_engine.token_budget import TokenCounter
except Exception:
    TokenCounter = None


class MemoryShadow(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        root = Path(os.environ.get("ASTRBOT_ROOT", ".")).resolve()
        base = root / "data" / "plugin_data" / "astrbot_plugin_memory_shadow"
        base.mkdir(parents=True, exist_ok=True)
        self.store = V15Store(str(base / "shadow.sqlite3"))
        self.engine = MemoryEngine(self.store)
        self.log = base / "shadow_events.jsonl"
        self.lock = threading.Lock()
        self.counter = TokenCounter() if TokenCounter is not None else None

    def _tokens(self, text):
        if self.counter is not None:
            return self.counter.count(text or "")
        return (len(text or "") + 3) // 4

    def _scope(self, event) -> str:
        origin = getattr(event, "unified_msg_origin", "") or "unknown"
        return "shadow-" + hashlib.sha256(origin.encode()).hexdigest()[:24]

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def capture(self, event):
        text = (getattr(event, "message_str", "") or "").strip()
        if not text or text.startswith(("/", "!")):
            return
        scope = self._scope(event)
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.lock:
            seq = self.store.db.execute(
                "SELECT COALESCE(MAX(source_seq),0)+1 FROM source_ref WHERE scope_id=?",
                (scope,)).fetchone()[0]
            self.engine.ingest(
                scope, SourceEnvelope(scope, seq, digest, "shadow", text))

    @filter.on_llm_request()
    async def observe(self, event, req):
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return
        scope = self._scope(event)
        total = int(os.environ.get("SHADOW_HOST_TOTAL_TOKENS", "8192"))
        reserve = int(os.environ.get("SHADOW_OUTPUT_TOKENS", "800"))
        history = json.dumps(getattr(req, "contexts", []) or [], ensure_ascii=False)
        budget = HostBudget(total, self._tokens(req.system_prompt),
                            self._tokens(history), reserve)
        packet = self.engine.build_rendered_context(
            scope, text, budget.memory_tokens, self.counter,
            host_budget=budget)
        record = {"ts": time.time(), "scope": scope, "query": text,
                  "items": len(packet["items"]),
                  "event_ids": [item["event_id"] for item in packet["items"]],
                  "host_memory_tokens": budget.memory_tokens,
                  "rendered_tokens": packet.get("rendered_tokens")}
        with self.lock:
            with self.log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def terminate(self):
        self.store.close()
