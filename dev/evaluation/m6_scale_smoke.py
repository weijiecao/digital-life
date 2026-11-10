"""M6 scale smoke: 10k derived events, lexical index build and query latency."""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.candidate_retriever import EventAnchor, LexicalCandidateIndex
from v1_5_consolidator.store import V15Store

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
QUERIES = 200


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        store = V15Store(str(Path(d) / "scale.sqlite3"))
        now = "2026-11-10T00:00:00+00:00"
        rows = []
        revisions = []
        evidence = []
        for i in range(N):
            eid = "evt_%05d" % i
            text = "记忆事件%d 主题词%d" % (i, i % 500)
            rows.append((eid, "s", "unknown", text, "provisional", 1,
                         "user", "user", "real", None, now, now))
            revisions.append((eid, 1, text, "unknown", "unknown", now))
            evidence.append((eid, 1, "s", i + 1, text, 1))
        with store.db:
            store.db.executemany("""INSERT INTO event_candidate
                (event_id,scope_id,event_type,title,state,current_revision,
                 narrator,subject,reality_status,story_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
            store.db.executemany("""INSERT INTO event_revision
                (event_id,rev,summary,status,modality,created_at)
                VALUES(?,?,?,?,?,?)""", revisions)
            store.db.executemany("""INSERT INTO event_evidence
                (event_id,rev,scope_id,source_seq,quote,valid)
                VALUES(?,?,?,?,?,?)""", evidence)
        anchors = [EventAnchor("evt_%05d" % i, "s", canonical_title="记忆事件%d 主题词%d" % (i, i % 500))
                   for i in range(N)]
        index = LexicalCandidateIndex()
        started = time.perf_counter()
        for anchor in anchors:
            index.add(anchor)
        build_ms = (time.perf_counter() - started) * 1000
        latencies = []
        hits = 0
        for q in range(QUERIES):
            term = "主题词%d" % (q % 500)
            t0 = time.perf_counter()
            result = index.search("s", term, k=10)
            latencies.append((time.perf_counter() - t0) * 1000)
            hits += len(result)
        latencies.sort()
        payload = {"events": N, "queries": QUERIES,
                   "index_build_ms": round(build_ms, 2),
                   "p50_query_ms": round(statistics.median(latencies), 3),
                   "p95_query_ms": round(latencies[int(len(latencies) * 0.95) - 1], 3),
                   "max_query_ms": round(max(latencies), 3),
                   "total_hits": hits,
                   "integrity": store.integrity_check()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_scale_smoke_%dk.json" % (N // 1000)).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
