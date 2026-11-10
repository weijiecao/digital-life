"""M6 multi-scope kill-and-resume smoke."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCOPES = int(sys.argv[2]) if len(sys.argv) > 2 else 4
PER_SCOPE = int(sys.argv[1]) if len(sys.argv) > 1 else 2000

CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
store = V15Store(sys.argv[1])
scope = sys.argv[3]
seq = int(sys.argv[4])
end = int(sys.argv[5])
while seq <= end:
    token = store.claim_scope(scope, 'child-' + scope, 30)
    if token is None:
        time.sleep(0.001)
        continue
    source = SourceEnvelope(scope, seq, 'sha', 't', 'm-%s-%d' % (scope, seq))
    with store.unit_of_work(scope, 'child-' + scope, token):
        event_id = store.create_event_from_source(
            scope, source, event_type='unknown', title=source.text[:80])
        store.attach_source(scope, source, event_id, 'write')
        store.record_receipt(scope, seq, 'ATTACHED', source.source_hash)
    store.release_scope(scope, 'child-' + scope, token)
    seq += 1
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "multi.sqlite3")
        from v1_5_consolidator.store import V15Store
        V15Store(db).close()
        code = "import time\n" + CHILD
        procs = [subprocess.Popen(
            [sys.executable, "-c", code, db, str(ROOT), "s%d" % i, "1", str(PER_SCOPE)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for i in range(SCOPES)]
        time.sleep(1.5)
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        store = V15Store(db)
        first = {scope: store.db.execute(
            "SELECT COALESCE(MAX(source_seq),0) FROM source_receipt WHERE scope_id=?",
            (scope,)).fetchone()[0] for scope in ("s%d" % i for i in range(SCOPES))}
        store.close()
        resume = []
        for scope, start in first.items():
            resume.append(subprocess.Popen(
                [sys.executable, "-c", code, db, str(ROOT), scope,
                 str(start + 1), str(PER_SCOPE)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        exits = []
        resume_errors = []
        for proc in resume:
            out, err = proc.communicate(timeout=900)
            exits.append(proc.returncode)
            if proc.returncode:
                resume_errors.append(err[-800:])
        check = V15Store(db)
        try:
            integrity = check.integrity_check()
            receipts = check.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0]
            evidence = check.db.execute("SELECT COUNT(*) FROM event_evidence WHERE valid=1").fetchone()[0]
            duplicates = check.db.execute("""SELECT COUNT(*) FROM (
                SELECT scope_id,source_seq,COUNT(*) c FROM source_receipt
                GROUP BY scope_id,source_seq HAVING c > 1)""").fetchone()[0]
            payload = {"scopes": SCOPES, "per_scope": PER_SCOPE,
                       "partial_after_kill": first, "resume_exits": exits,
                       "receipts": receipts, "evidence": evidence,
                       "duplicates": duplicates, "integrity": integrity,
                       "resume_errors": resume_errors}
        finally:
            check.close()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_kill_resume_multi_%dk.json" % ((SCOPES * PER_SCOPE) // 1000)).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        ok = (receipts == SCOPES * PER_SCOPE and evidence == SCOPES * PER_SCOPE
              and duplicates == 0 and integrity == "ok" and all(v == 0 for v in exits))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
