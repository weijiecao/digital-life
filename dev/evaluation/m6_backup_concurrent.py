"""M6 backup while a writer is active."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v1_5_consolidator.store import V15Store

ROOT = Path(__file__).resolve().parents[1]
TOTAL = 5000
CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
store = V15Store(sys.argv[1])
for seq in range(1, int(sys.argv[3]) + 1):
    token = store.claim_scope('s', 'writer', 30)
    while token is None:
        time.sleep(0.001)
        token = store.claim_scope('s', 'writer', 30)
    source = SourceEnvelope('s', seq, 'sha', 't', 'b-%d' % seq)
    with store.unit_of_work('s', 'writer', token):
        event_id = store.create_event_from_source(
            's', source, event_type='unknown', title=source.text[:80])
        store.attach_source('s', source, event_id, 'write')
        store.record_receipt('s', seq, 'ATTACHED', source.source_hash)
    store.release_scope('s', 'writer', token)
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "live.sqlite3")
        V15Store(db).close()
        code = "import time\n" + CHILD
        proc = subprocess.Popen([sys.executable, "-c", code, db, str(ROOT), str(TOTAL)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        reader = V15Store(db)
        counts = []
        errors = []
        index = 0
        try:
            while proc.poll() is None and index < 20:
                backup = str(Path(d) / ("b-%02d.sqlite3" % index))
                try:
                    reader.backup_to(backup)
                    check = V15Store(backup)
                    try:
                        counts.append(check.db.execute(
                            "SELECT COUNT(*) FROM source_receipt").fetchone()[0])
                        if check.integrity_check() != "ok":
                            errors.append("integrity")
                    finally:
                        check.close()
                except Exception as exc:
                    errors.append(repr(exc))
                index += 1
                time.sleep(0.2)
        finally:
            reader.close()
            try:
                _, stderr = proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate()
                errors.append("writer timeout")
        payload = {"writer_exit": proc.returncode, "backups": len(counts),
                   "counts": counts, "monotonic": counts == sorted(counts),
                   "errors": errors, "writer_stderr": (stderr or "")[-300:]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        Path(__file__).resolve().parent.parent.joinpath(
            "reports", "m6_backup_concurrent.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if (proc.returncode == 0 and not errors and payload["monotonic"]
                     and counts and counts[-1] > 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
