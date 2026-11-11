"""Closed-loop smoke: automatic queue, retry, restart, cache, budget, long run."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from memory_engine.engine import MemoryEngine
from memory_engine.rejudge import PendingRejudger
from memory_engine.token_budget import TokenCounter
from memory_engine.worker import ReadyWorker
from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.store import V15Store


def source(scope: str, seq: int, text: str) -> SourceEnvelope:
    import hashlib
    return SourceEnvelope(scope, seq, hashlib.sha256(text.encode()).hexdigest(), "t", text)


def run() -> dict:
    checks = {}
    with tempfile.TemporaryDirectory(prefix="closed-loop-") as directory:
        db = Path(directory) / "loop.sqlite3"
        store = V15Store(db)
        engine = MemoryEngine(store)
        for seq in range(1, 31):
            engine.ingest("s", source("s", seq, f"第{seq}条安排，地点西入口。"),
                          provenance={"narrator": "user", "subject": "user",
                                      "reality_status": "real", "story_id": None})
        target = engine.ingest("s", source("s", 31, "木桥市集最终确认西入口。"),
                               provenance={"narrator": "user", "subject": "user",
                                           "reality_status": "real", "story_id": None}).event_id
        pending = source("s", 32, "补充：木桥市集最终确认西入口。")
        store.save_pending("s", 32, [{"candidate_event_id": target,
                                      "label": "SAME_EVENT"}],
                           {target: [target]}, "dependency_wait", source=pending,
                           provenance=NarrativeProvenance("user", "user", "real"))
        store.update_dependency("s", "event", target, 1, [32], "candidate_changed")
        attempts = {"n": 0}

        def judge(scope, src):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise TimeoutError("provider timeout")
            return JudgmentResult(source_seq=src.source_seq,
                                  proposals=[RelationProposal(target, "SAME_EVENT")],
                                  provenance=NarrativeProvenance("user", "user", "real"))

        result = PendingRejudger(store, judge).run_once("s", attempts=2)
        checks["retry_then_resolve"] = (attempts["n"] == 2 and result["resolved"] == 1
                                        and store.ready_generation("s", 32) is None)
        checks["pending_relocated"] = store.pending_for_source("s", 32)["status"] == "RELOCATED"

        for seq in range(40, 60):
            store.enqueue_ready("s", seq, "automatic")
        worker_attempts = {"n": 0}

        def evaluate(scope, seq):
            worker_attempts["n"] += 1
            if seq % 2 == 0 and worker_attempts["n"] <= 2:
                raise RuntimeError("temporary")
            return {"outcome": "DONE"}

        worker = ReadyWorker(store)
        rounds = [worker.run_once("s", evaluate) for _ in range(3)]
        checks["automatic_queue_consumed"] = all(
            item["source_seq"] not in [r["source_seq"] for r in store.poll_ready("s", 100)]
            for item in [{"source_seq": seq} for seq in range(40, 60)])
        checks["retry_kept_work"] = sum(r["failed"] for r in rounds) >= 1

        warm = engine.build_context("s", "木桥市集")
        store.attach_source("s", source("s", 70, "木桥市集旁边的茶摊。"),
                            target, "closed-loop-extra")
        after = engine.build_context("s", "木桥市集")
        checks["cache_refresh_on_attach"] = (warm != after)

        store.close()
        reopened = V15Store(db)
        reopened_engine = MemoryEngine(reopened)
        restarted = reopened_engine.build_context("s", "木桥市集")
        checks["restart_visible"] = bool(restarted["items"])
        checks["integrity"] = reopened.integrity_check() == "ok"

        counter = TokenCounter()
        packet = reopened_engine.build_rendered_context(
            "s", "木桥市集", budget_tokens=120, token_counter=counter)
        checks["budget_bounded"] = (counter.count(packet["rendered"]) <= 120
                                    and packet["rendered"] != "")
        empty = reopened_engine.build_rendered_context(
            "s", "木桥市集", budget_tokens=0, token_counter=counter)
        checks["zero_budget_empty"] = empty["rendered"] == ""

        for _ in range(200):
            reopened_engine.build_context("s", "木桥市集", budget=300)
        checks["long_run_queries"] = reopened_engine.store.integrity_check() == "ok"
        checks["cache_bounded"] = reopened_engine.cache.size() <= 4
        reopened.close()
    return {"pass": all(checks.values()), "checks": checks}


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["pass"] else 1)
