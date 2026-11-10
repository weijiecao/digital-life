"""M6 kill-during-write smoke: partial DB must stay consistent."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CHILD = r"""
import sys
sys.path.insert(0, sys.argv[2])
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
store = V15Store(sys.argv[1])
seq = 1
while True:
    token = store.claim_scope('s', 'child', 30)
    if token is None:
        time.sleep(0.001)
        continue
    source = SourceEnvelope('s', seq, 'sha', 't', 'crash-%d' % seq)
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
        db = str(Path(d) / "kill.sqlite3")
        code = "import time\n" + CHILD
        proc = subprocess.Popen([sys.executable, "-c", code, db, str(ROOT)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.0)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        from v1_5_consolidator.store import V15Store
        store = V15Store(db)
        try:
            integrity = store.integrity_check()
            evidence = store.db.execute(
                "SELECT COUNT(*) FROM event_evidence WHERE valid=1").fetchone()[0]
            receipts = store.db.execute("SELECT COUNT(*) FROM source_receipt").fetchone()[0]
            duplicate_sources = store.db.execute("""SELECT COUNT(*) FROM (
                SELECT source_seq, COUNT(*) c FROM source_receipt
                GROUP BY source_seq HAVING c > 1)""").fetchone()[0]
            payload = {"exit": proc.returncode, "integrity": integrity,
                       "evidence": evidence, "receipts": receipts,
                       "partial": evidence < 1000,
                       "duplicate_sources": duplicate_sources}
        finally:
            store.close()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_kill_recovery.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if integrity == "ok" and evidence == receipts and duplicate_sources == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
