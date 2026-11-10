"""Small background worker for the M1 READY queue.

The evaluator is injected so the worker does not call a model and does not
decide attachment itself. This is the M1 scheduling/consumption primitive.
"""

from __future__ import annotations

from typing import Callable

from v1_5_consolidator.store import V15Store


class ReadyWorker:
    def __init__(self, store: V15Store):
        self.store = store

    def run_once(self, scope_id: str, evaluate: Callable[[str, int], dict],
                 limit: int = 100) -> dict:
        processed = 0
        failed = 0
        for item in self.store.poll_ready(scope_id, limit):
            source_seq = item["source_seq"]
            try:
                result = evaluate(scope_id, source_seq)
            except Exception as exc:
                result = {"outcome": "RETRY", "error": str(exc)}
            if result.get("outcome") == "RETRY":
                failed += 1
                continue
            with self.store.unit_of_work():
                self.store.mark_ready_done(scope_id, source_seq)
            processed += 1
        return {"processed": processed, "failed": failed}
