"""E-E closed-loop replay: compare final semantic DB across batch sizes."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.b4_frozen_replay import run
from v1_5_consolidator.store import V15Store

ROOT = Path(__file__).resolve().parents[1]
TRACE = ROOT / "reports" / "v1_5b_b4_cluster_prov_trace.json"
FIXTURE = ROOT / "evaluation" / "b4_testdata" / "Digital_Life_B4_TestData_v1"


def snapshot(db_path: str) -> dict:
    store = V15Store(db_path)
    try:
        membership = {}
        for scope, seq in store.db.execute(
                "SELECT scope_id, source_seq FROM source_ref ORDER BY scope_id, source_seq"):
            events = store.events_for_source(scope, seq)
            membership["%s:%d" % (scope, seq)] = sorted(events)
        groups = {}
        for source, events in membership.items():
            groups.setdefault(tuple(events), []).append(source)
        partition = sorted(tuple(sorted(v)) for v in groups.values())
        pending = sorted((scope, seq, status) for scope, seq, status in
                         store.db.execute("SELECT scope_id,source_seq,status FROM resolution_pending"))
        receipts = sorted((scope, seq, outcome, input_hash) for scope, seq, outcome, input_hash in
                          store.db.execute("""SELECT scope_id,source_seq,outcome,input_hash
                              FROM source_receipt"""))
        valid = store.db.execute("SELECT COUNT(*) FROM event_evidence WHERE valid=1").fetchone()[0]
        events = store.db.execute("SELECT COUNT(*) FROM event_candidate").fetchone()[0]
        return {"partition": partition, "pending": pending,
                "receipts": receipts, "valid_evidence": valid,
                "events": events}
    finally:
        store.close()


def main() -> int:
    output = {}
    with tempfile.TemporaryDirectory(prefix="ee-") as d:
        for batch in (8, 20, 40):
            db = str(Path(d) / ("batch-%d.sqlite3" % batch))
            run(str(FIXTURE), str(TRACE), batch, db)
            output[str(batch)] = snapshot(db)
    base = output["8"]
    comparison = {key: {"same": output[key] == base,
                        "diff": [k for k in base if output[key][k] != base[k]]}
                  for key in ("20", "40")}
    with tempfile.TemporaryDirectory(prefix="ee-repeat-") as d:
        db = str(Path(d) / "repeat.sqlite3")
        run(str(FIXTURE), str(TRACE), 20, db)
        first = snapshot(db)
        run(str(FIXTURE), str(TRACE), 20, db)
        second = snapshot(db)
    repeat_same = first == second
    payload = {"suite": "e-e-closed-loop", "batch_sizes": [8, 20, 40],
               "comparison": comparison, "repeat_same_db": repeat_same}
    print(json.dumps({"comparison": comparison,
                      "repeat_same_db": repeat_same}, ensure_ascii=False, indent=2))
    (ROOT / "reports" / "e2e_closed_loop.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(v["same"] for v in comparison.values()) and repeat_same else 1


if __name__ == "__main__":
    raise SystemExit(main())
