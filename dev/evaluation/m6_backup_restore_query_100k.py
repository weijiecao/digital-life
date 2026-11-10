"""M6 100k backup -> restore -> real query/context smoke."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_engine.engine import MemoryEngine
from v1_5_consolidator.store import V15Store

N = 100000
NOW = "2026-11-11T00:00:00+00:00"


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "main.sqlite3")
        store = V15Store(db)
        started = time.perf_counter()
        events = [("evt_%06d" % i, "s", "unknown", "记忆事件%d 主题词%d" % (i, i % 500),
                   "provisional", 1, "user", "user", "real", None, NOW, NOW)
                  for i in range(N)]
        revisions = [("evt_%06d" % i, 1, "摘要%d" % i, "active", "text",
                      NOW, "", NOW, "bulk", "bulk", "sha") for i in range(N)]
        evidence = [("evt_%06d" % i, 1, "s", i + 1, "原文%d" % i, 1)
                    for i in range(N)]
        with store.db:
            store.db.executemany("""INSERT INTO event_candidate
                (event_id,scope_id,event_type,title,state,current_revision,
                 narrator,subject,reality_status,story_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", events)
            store.db.executemany("""INSERT INTO event_revision
                (event_id,rev,summary,status,modality,occurred_at,
                 time_expression_raw,created_at,model_version,prompt_version,input_digest)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", revisions)
            store.db.executemany("""INSERT INTO event_evidence
                (event_id,rev,scope_id,source_seq,quote,valid)
                VALUES(?,?,?,?,?,?)""", evidence)
        build_ms = (time.perf_counter() - started) * 1000
        backup = str(Path(d) / "backup.sqlite3")
        store.backup_to(backup)
        store.close()
        restored = V15Store(backup)
        engine = MemoryEngine(restored)
        backup_count = restored.db.execute(
            "SELECT COUNT(*) FROM event_candidate").fetchone()[0]
        t0 = time.perf_counter()
        hits = engine.retrieve("s", "主题词1", limit=10)
        query_ms = (time.perf_counter() - t0) * 1000
        packet = engine.build_context("s", "主题词1", budget=1200)
        from v1_5_consolidator.contracts import SourceEnvelope
        engine.ingest("s", SourceEnvelope("s", N + 1, "sha-new", "t", "恢复后新增"))
        write_hit = len(engine.retrieve("s", "恢复后新增"))
        payload = {"events": N, "build_ms": round(build_ms, 2),
                   "backup_events": backup_count,
                   "retrieve_hits": len(hits), "query_ms": round(query_ms, 2),
                   "context_items": len(packet["items"]),
                   "write_after_restore": write_hit,
                   "receipts": restored.db.execute(
                       "SELECT COUNT(*) FROM source_receipt").fetchone()[0],
                   "integrity": restored.integrity_check()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_backup_restore_query_100k.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        restored.close()
        return 0 if (payload["backup_events"] == N and hits
                     and payload["write_after_restore"] == 1
                     and payload["integrity"] == "ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
