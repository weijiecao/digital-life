"""M6 100k concurrent-reader smoke (read-only, no model)."""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.store import V15Store

N = 100000
READERS = 4
QUERIES_PER_READER = 50


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "scale.sqlite3")
        store = V15Store(db_path)
        now = "2026-11-10T00:00:00+00:00"
        rows = []
        for i in range(N):
            rows.append(("evt_%06d" % i, "s", "unknown",
                         "记忆事件%d 主题词%d" % (i, i % 500), "provisional", 1,
                         "user", "user", "real", None, now, now))
        with store.db:
            store.db.executemany("""INSERT INTO event_candidate
                (event_id,scope_id,event_type,title,state,current_revision,
                 narrator,subject,reality_status,story_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        latencies = []
        errors = []
        lock = threading.Lock()

        def reader(reader_id):
            local = []
            conn = V15Store(db_path)
            try:
                for q in range(QUERIES_PER_READER):
                    term = "主题词%d" % ((reader_id * 17 + q) % 500)
                    start = time.perf_counter()
                    try:
                        conn.db.execute("""SELECT event_id FROM event_candidate
                            WHERE scope_id='s' AND title LIKE ? LIMIT 10""",
                            ("%" + term + "%",)).fetchall()
                    except Exception as exc:
                        with lock:
                            errors.append(str(exc))
                    local.append((time.perf_counter() - start) * 1000)
            finally:
                conn.close()
            with lock:
                latencies.extend(local)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(READERS)]
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = (time.perf_counter() - started) * 1000
        latencies.sort()
        payload = {"events": N, "readers": READERS,
                   "queries": READERS * QUERIES_PER_READER,
                   "wall_ms": round(elapsed, 2),
                   "p50_ms": round(statistics.median(latencies), 3),
                   "p95_ms": round(latencies[int(len(latencies) * 0.95) - 1], 3),
                   "max_ms": round(max(latencies), 3),
                   "errors": len(errors),
                   "integrity": store.integrity_check()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_concurrent_100k.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
