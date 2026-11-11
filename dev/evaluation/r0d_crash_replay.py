"""R0-D crash tests: kill before commit and after commit/before report."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v1_5_consolidator.store import V15Store

REPLAY = ROOT / "evaluation" / "b4_frozen_replay.py"
FIXTURE = ROOT / "evaluation" / "b4_testdata" / "Digital_Life_B4_TestData_v1"
TRACE = ROOT / "reports" / "v1_5b_b4_cluster_prov_trace.json"
PY = sys.executable


def counts(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    try:
        def one(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])
        scopes = [r[0] for r in conn.execute(
            "SELECT DISTINCT scope_id FROM source_manifest ORDER BY scope_id")]
        store = V15Store(str(db))
        try:
            frontier = {scope: store.contiguous_frontier(scope) for scope in scopes}
        finally:
            store.close()
        return {"receipts": one("SELECT COUNT(*) FROM source_receipt"),
                "events": one("SELECT COUNT(*) FROM event_candidate"),
                "evidence": one("SELECT COUNT(*) FROM event_evidence WHERE valid=1"),
                "proposals": one("SELECT COUNT(*) FROM event_link_proposal"),
                "decisions": one("SELECT COUNT(*) FROM event_link_decision"),
                "frontier": frontier}
    finally:
        conn.close()


def crash_run(db: Path, crash_at: str) -> dict:
    proc = subprocess.run(
        [PY, str(REPLAY), "--root", str(FIXTURE), "--trace", str(TRACE),
         "--batch-size", "8", "--db", str(db), "--out", str(db) + ".crash.json",
         "--crash-at", crash_at, "--lease-ttl", "1"],
        capture_output=True, text=True, timeout=120)
    return {"exit": proc.returncode, "stdout": proc.stdout[-500:],
            "stderr": proc.stderr[-500:]}


def resume(db: Path, out: Path) -> dict:
    proc = subprocess.run(
        [PY, str(REPLAY), "--root", str(FIXTURE), "--trace", str(TRACE),
         "--batch-size", "8", "--db", str(db), "--out", str(out)],
        capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    data = json.loads(out.read_text(encoding="utf-8"))
    return {"exit": proc.returncode, "messages": data["messages"],
            "already_receipted": data["already_receipted"],
            "recovery_scan": data["recovery_scan"],
            "receipt_hits": data["receipt_hits"],
            "scope_claim_failed": data["scope_claim_failed"],
            "job_claim_failed": data["job_claim_failed"],
            "diff": len(data["diff"])}


def run_case(crash_at: str, label: str, tmp: Path) -> dict:
    db = tmp / (label + ".sqlite3")
    crash = crash_run(db, crash_at)
    before_resume = counts(db)
    time.sleep(1.4)
    resumed = resume(db, tmp / (label + ".json"))
    after = counts(db)
    return {"label": label, "crash": crash, "before_resume": before_resume,
            "resume": resumed, "after": after}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r0d-crash-") as d:
        tmp = Path(d)
        result = {
            "before_commit": run_case("before_commit", "before_commit", tmp),
            "after_commit": run_case("after_commit", "after_commit", tmp),
        }
    checks = {
        "before_crash_no_receipt": result["before_commit"]["before_resume"]["receipts"] == 0,
        "before_resume_completes_340": result["before_commit"]["after"]["receipts"] == 340,
        "before_resume_no_duplicates": result["before_commit"]["after"]["evidence"] == 340,
        "after_crash_one_receipt": result["after_commit"]["before_resume"]["receipts"] == 1,
        "after_resume_340": result["after_commit"]["after"]["receipts"] == 340,
        "after_resume_no_duplicates": result["after_commit"]["after"]["evidence"] == 340,
        "after_resume_skips_first": result["after_commit"]["resume"]["already_receipted"] >= 1,
        "both_leases_claimed": all(
            result[k]["resume"]["scope_claim_failed"] == 0
            for k in ("before_commit", "after_commit")),
        "frontier_34_all": all(
            set(result[k]["after"]["frontier"].values()) == {34}
            for k in ("before_commit", "after_commit")),
    }
    payload = {"checks": checks, "pass": all(checks.values()), "cases": result}
    out = ROOT / "reports" / "r0d_crash_evidence.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pass": payload["pass"], "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
