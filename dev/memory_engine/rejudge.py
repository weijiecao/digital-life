"""Pending re-evaluation service with an injected judgment provider."""

from __future__ import annotations

from typing import Callable

from v1_5_consolidator.relation_judge import JudgmentResult
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


class PendingRejudger:
    def __init__(self, store: V15Store,
                 judge: Callable[[str, object], JudgmentResult]):
        self.store = store
        self.judge = judge
        self.resolver = ConstrainedResolver(store)

    def run_once(self, scope_id: str, limit: int = 100) -> dict:
        processed = 0
        resolved = 0
        deferred = 0
        for item in self.store.poll_ready(scope_id, limit):
            source = self.store.pending_source(scope_id, item["source_seq"])
            if source is None:
                self.store.mark_ready_done(scope_id, item["source_seq"])
                continue
            judgment = self.judge(scope_id, source)
            with self.store.unit_of_work():
                summary = self.resolver.resolve(scope_id, source, judgment)
                events = self.store.events_for_source(scope_id, item["source_seq"])
                if events and (summary.attached or summary.already_attached):
                    self.store.mark_pending_relocated(
                        scope_id, item["source_seq"], events[0])
                    resolved += 1
                else:
                    deferred += 1
                self.store.mark_ready_done(
                    scope_id, item["source_seq"],
                    expected_generation=item["generation"])
            processed += 1
        return {"processed": processed, "resolved": resolved, "deferred": deferred}


if __name__ == "__main__":
    raise SystemExit("PendingRejudger requires an injected judge provider")
