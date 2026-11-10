"""M6 kill-and-resume: finish the remaining sources and verify one per source."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TOTAL = 5000

CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
store = V15Store(sys.argv[1])
seq = int(sys.argv[3])
end = int(sys.argv[4])
while seq <= end:
    token = store.claim_scope('s', 'child', 30)
    if token is None:
        time.sleep(0.001)
        continue
    source = SourceEnvelope('s', seq, 'sha', 't', 'resume-%d' % seq)
    with store.unit_of_work('s', 'child', token):
        event_id = store.create_event_from_source(
            's', source, event_type='unknown', title=source.text[:80])
        store.attach_source('s', source, event_id, 'write')
        store.record_receipt('s', seq, 'ATTACHED', source.source_hash)
    store.release_scope('s', 'child', token)
    seq += 1
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "resume.sqlite3")
        code = "import time\n" + CHILD
        proc = subprocess.Popen([sys.executable, "-c", code, db, str(ROOT), "1", str(TOTAL)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        from v1_5_consolidator.store import V15Store
        store = V15Store(db)
        first = store.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0]
        store.close()
        code2 = "import time\n" + CHILD
        done = subprocess.run([sys.executable, "-c", code2, db, str(ROOT),
                               str(first + 1), str(TOTAL)],
                              capture_output=True, text=True)
        check = V15Store(db)
        try:
            integrity = check.integrity_check()
            receipts = check.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0]
            evidence = check.db.execute("SELECT COUNT(*) FROM event_evidence WHERE valid=1").fetchone()[0]
            duplicates = check.db.execute("""SELECT COUNT(*) FROM (
                SELECT source_seq, COUNT(*) c FROM source_receipt
                GROUP BY source_seq HAVING c > 1)""").fetchone()[0]
            source_refs = check.db.execute("SELECT COUNT(*) FROM source_ref").fetchone()[0]
            payload = {"partial_after_kill": first, "resume_exit": done.returncode,
                       "receipts": receipts, "evidence": evidence,
                       "source_refs": source_refs,
                       "duplicates": duplicates, "integrity": integrity}
        finally:
            check.close()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_kill_resume.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        ok = (receipts == TOTAL and evidence == TOTAL and source_refs == TOTAL
              and duplicates == 0 and integrity == "ok" and done.returncode == 0)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
