"""M6 write-concurrency smoke: multiple workers, one scope lease."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store

WORKERS = 4
SOURCES = 20


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "write.sqlite3")
        V15Store(db_path).close()
        errors = []
        latencies = []
        lock = threading.Lock()
        pending = list(range(1, SOURCES + 1))

        def worker(worker_id):
            store = V15Store(db_path)
            try:
                while True:
                    with lock:
                        if not pending:
                            return
                        seq = pending.pop()
                    start = time.perf_counter()
                    token = None
                    for _ in range(500):
                        try:
                            token = store.claim_scope("s", "w%d" % worker_id, 30)
                        except Exception as exc:
                            with lock:
                                errors.append(str(exc))
                            time.sleep(0.01)
                            continue
                        if token is not None:
                            break
                        time.sleep(0.005)
                    if token is None:
                        with lock:
                            errors.append("lease timeout")
                        return
                    try:
                        source = SourceEnvelope("s", seq, "sha", "t", "write-%d" % seq)
                        with store.unit_of_work("s", "w%d" % worker_id, token):
                            event_id = store.create_event_from_source(
                                "s", source, event_type="unknown",
                                title=source.text[:80])
                            store.attach_source("s", source, event_id, "write")
                            store.record_receipt("s", seq, "ATTACHED", source.source_hash)
                    except Exception as exc:
                        with lock:
                            errors.append(str(exc))
                    finally:
                        store.release_scope("s", "w%d" % worker_id, token)
                    with lock:
                        latencies.append((time.perf_counter() - start) * 1000)
            finally:
                store.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS)]
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = (time.perf_counter() - started) * 1000
        check = V15Store(db_path)
        evidence = check.db.execute("SELECT COUNT(*) FROM event_evidence WHERE valid=1").fetchone()[0]
        receipts = check.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0]
        integrity = check.integrity_check()
        check.close()
        latencies.sort()
        payload = {"workers": WORKERS, "sources": SOURCES,
                   "wall_ms": round(elapsed, 2),
                   "p95_ms": round(latencies[int(len(latencies) * 0.95) - 1], 3)
                   if latencies else None,
                   "errors": len(errors), "evidence": evidence,
                   "receipts": receipts, "integrity": integrity}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_write_concurrency.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if not errors and evidence == SOURCES and receipts == SOURCES else 1


if __name__ == "__main__":
    raise SystemExit(main())
