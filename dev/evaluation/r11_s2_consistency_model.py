"""R11-S2 reliability model prototype.

This is an offline model of a single consistency unit:
claim -> map proposals -> pending/guard/resolve -> evidence/audit -> cursor.
It uses a standalone SQLite schema, not V15Store, so the result is a model
validation, not a claim that the current store already provides one transaction.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE jobs(job_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL,
  owner TEXT, token INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
  cursor INTEGER NOT NULL DEFAULT 0, expires_at REAL NOT NULL DEFAULT 0);
CREATE TABLE events(event_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL);
CREATE TABLE evidence(scope_id TEXT NOT NULL, source_id TEXT NOT NULL,
  event_id TEXT NOT NULL, PRIMARY KEY(scope_id, source_id, event_id));
CREATE TABLE decisions(scope_id TEXT NOT NULL, source_id TEXT NOT NULL,
  action TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(scope_id, source_id));
CREATE TABLE pending(scope_id TEXT NOT NULL, source_id TEXT NOT NULL,
  proposals_json TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
  PRIMARY KEY(scope_id, source_id));
CREATE TABLE audit(scope_id TEXT NOT NULL, source_id TEXT NOT NULL,
  action TEXT NOT NULL, resolver TEXT NOT NULL);
CREATE TABLE processed(scope_id TEXT NOT NULL, source_id TEXT NOT NULL,
  PRIMARY KEY(scope_id, source_id));
"""


def connect(path=None):
    path = path or ":memory:"
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.execute("INSERT INTO jobs VALUES('job-1','s',NULL,0,'OPEN',0,0)")
    db.commit()
    return db


def claim(db, owner, now=None):
    now = time.time() if now is None else now
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT owner,token,status,expires_at FROM jobs WHERE job_id='job-1'").fetchone()
    if row["status"] == "DONE":
        db.rollback()
        return None
    if row["owner"] is not None and row["expires_at"] > now:
        db.rollback()
        return None
    token = row["token"] + 1
    db.execute("UPDATE jobs SET owner=?,token=?,status='CLAIMED',expires_at=? WHERE job_id='job-1'",
               (owner, token, now + 5))
    db.commit()
    return token


def process(db, source_id, proposals, mapping, guard="ALLOW", crash_before_commit=False):
    """One source = one transaction; all business writes share it."""
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM processed WHERE scope_id='s' AND source_id=?",
                      (source_id,)).fetchone():
            db.commit()
            return "ALREADY"
        full = json.dumps(proposals, ensure_ascii=False, sort_keys=True)
        statuses = []
        for old, label in proposals:
            targets = mapping.get(old, set())
            statuses.append((old, label, len(targets)))
        if any(count != 1 for _, _, count in statuses):
            db.execute("""INSERT OR REPLACE INTO pending
                (scope_id,source_id,proposals_json,status,reason)
                VALUES('s',?,?,'PENDING','MAPPING_INCOMPLETE')""",
                (source_id, full))
            db.execute("INSERT INTO decisions VALUES('s',?, 'DEFER', 'MAPPING_INCOMPLETE')",
                       (source_id,))
            db.execute("INSERT INTO audit VALUES('s',?, 'NOT_APPLIED', 'DEFER_MAPPING')",
                       (source_id,))
            db.execute("INSERT INTO processed VALUES('s',?)", (source_id,))
            if crash_before_commit:
                raise RuntimeError("crash-before-commit")
            db.commit()
            return "DEFER_MAPPING"
        same = [label for _, label in proposals if label == "SAME_EVENT"]
        if len(same) > 1:
            db.execute("INSERT INTO decisions VALUES('s',?, 'CONFLICT', 'MULTI_SAME')",
                       (source_id,))
            db.execute("INSERT INTO audit VALUES('s',?, 'NOT_APPLIED', 'CONFLICT_MULTI')",
                       (source_id,))
            db.execute("INSERT INTO processed VALUES('s',?)", (source_id,))
            if crash_before_commit:
                raise RuntimeError("crash-before-commit")
            db.commit()
            return "CONFLICT_MULTI"
        if same:
            target = next(iter(mapping[proposals[0][0]]))
            if guard != "ALLOW":
                db.execute("INSERT INTO decisions VALUES('s',?, 'DEFER', ?)",
                           (source_id, "GUARD_" + guard))
                db.execute("INSERT INTO audit VALUES('s',?, ?, 'DEFER_GUARD')",
                           (source_id, guard))
                db.execute("INSERT INTO processed VALUES('s',?)", (source_id,))
                if crash_before_commit:
                    raise RuntimeError("crash-before-commit")
                db.commit()
                return "DEFER_GUARD"
            db.execute("INSERT OR IGNORE INTO events VALUES(?, 's')", (target,))
            db.execute("INSERT INTO evidence VALUES('s',?,?)", (source_id, target))
            db.execute("INSERT INTO decisions VALUES('s',?, 'ATTACHED', 'SAME')", (source_id,))
            db.execute("INSERT INTO audit VALUES('s',?, 'ALLOW', 'ATTACHED')", (source_id,))
        else:
            db.execute("INSERT INTO decisions VALUES('s',?, 'NO_ATTACH', 'NON_SAME')",
                       (source_id,))
            db.execute("INSERT INTO audit VALUES('s',?, 'NOT_APPLIED', 'NO_ATTACH')",
                       (source_id,))
        db.execute("INSERT INTO processed VALUES('s',?)", (source_id,))
        db.execute("UPDATE jobs SET cursor=MAX(cursor, CAST(? AS INTEGER)) WHERE job_id='job-1'",
                   (source_id,))
        if crash_before_commit:
            raise RuntimeError("crash-before-commit")
        db.commit()
        return "ATTACHED" if same else "NO_ATTACH"
    except Exception:
        db.rollback()
        raise


def relocate(db, source_id, new_event):
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT proposals_json FROM pending WHERE scope_id='s' AND source_id=?",
                         (source_id,)).fetchone()
        if not row:
            db.rollback()
            return "NO_PENDING"
        db.execute("INSERT OR IGNORE INTO events VALUES(?, 's')", (new_event,))
        db.execute("INSERT INTO evidence VALUES('s',?,?)", (source_id, new_event))
        db.execute("UPDATE pending SET status='RESOLVED', reason='RELOCATED' WHERE scope_id='s' AND source_id=?",
                   (source_id,))
        db.execute("INSERT INTO audit VALUES('s',?, 'RELOCATED', 'ATTACHED')", (source_id,))
        db.commit()
        return "RELOCATED"
    except Exception:
        db.rollback()
        raise


def main() -> int:
    out = {"claims": {}, "rollback": {}, "idempotent": {}, "pending": {},
           "recovery": {}, "cursor": {}}
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "model.sqlite3")
        db = connect(path)
        t1 = claim(db, "worker-A")
        t2 = claim(db, "worker-B")
        out["claims"] = {"first": t1, "second": t2}
        assert t1 == 1 and t2 is None
        # rollback: source-1, crash before commit
        try:
            process(db, "1", [(("a"), "SAME_EVENT")], {"a": {"evt-1"}}, crash_before_commit=True)
        except RuntimeError:
            pass
        out["rollback"] = {t: db.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                           for t in ("events", "evidence", "decisions", "pending", "processed")}
        assert all(v == 0 for v in out["rollback"].values())
        # idempotent
        r1 = process(db, "1", [("a", "SAME_EVENT")], {"a": {"evt-1"}})
        r2 = process(db, "1", [("a", "SAME_EVENT")], {"a": {"evt-1"}})
        out["idempotent"] = {"first": r1, "second": r2,
                             "evidence": db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]}
        assert out["idempotent"]["evidence"] == 1
        # fork pending + full proposals + relocation
        full = [{"candidate_event_id": "old-E", "label": "SAME_EVENT"},
                {"candidate_event_id": "old-F", "label": "DIFFERENT_EVENT"}]
        r3 = process(db, "2", [(p["candidate_event_id"], p["label"]) for p in full],
                     {"old-E": {"new-A", "new-B"}, "old-F": {"new-C"}})
        saved = json.loads(db.execute(
            "SELECT proposals_json FROM pending WHERE source_id='2'").fetchone()[0])
        r4 = relocate(db, "2", "new-C")
        out["pending"] = {"decision": r3, "saved": saved, "relocate": r4,
                          "evidence": db.execute("SELECT COUNT(*) FROM evidence WHERE source_id='2'").fetchone()[0],
                          "status": db.execute("SELECT status FROM pending WHERE source_id='2'").fetchone()[0]}
        assert r3 == "DEFER_MAPPING" and r4 == "RELOCATED" and len(saved) == 2
        # crash recovery: new worker claims expired, source not partially written
        db.execute("UPDATE jobs SET expires_at=0 WHERE job_id='job-1'")
        db.commit()
        t3 = claim(db, "worker-C")
        assert t3 == t1 + 1
        try:
            process(db, "3", [("a", "SAME_EVENT")], {"a": {"evt-3"}}, crash_before_commit=True)
        except RuntimeError:
            pass
        t4 = claim(db, "worker-D")
        # expiry was 5s; force expiry then takeover
        db.execute("UPDATE jobs SET expires_at=0 WHERE job_id='job-1'")
        db.commit()
        t5 = claim(db, "worker-E")
        out["recovery"] = {"claim_after_crash": t3, "takeover": t5,
                           "source3_evidence": db.execute("SELECT COUNT(*) FROM evidence WHERE source_id='3'").fetchone()[0],
                           "cursor": db.execute("SELECT cursor FROM jobs").fetchone()[0]}
        assert out["recovery"]["source3_evidence"] == 0
        # cursor/repeat
        r5 = process(db, "1", [("a", "SAME_EVENT")], {"a": {"evt-1"}})
        out["cursor"] = {"repeat": r5, "cursor": db.execute("SELECT cursor FROM jobs").fetchone()[0],
                         "evidence": db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]}
        assert r5 == "ALREADY"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
