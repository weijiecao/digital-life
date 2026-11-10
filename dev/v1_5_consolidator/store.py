"""V1.5 independent SQLite store.

The store only holds derived candidates/evidence/jobs. It never opens the V1
database for writing and never creates V1 formal events.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Iterable

SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class V15Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self._unit_depth = 0
        self._unit_rollback_only = False
        self._init_schema()

    def _init_schema(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_ref(
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, source_hash TEXT NOT NULL,
          recorded_at TEXT NOT NULL, deleted_at TEXT,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS source_inventory(
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, source_hash TEXT NOT NULL,
          last_seen TEXT NOT NULL, deleted_at TEXT,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS consolidation_cursor(
          scope_id TEXT PRIMARY KEY, last_committed_seq INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS consolidation_job(
          job_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, from_seq INTEGER NOT NULL,
          to_seq INTEGER NOT NULL, input_digest TEXT NOT NULL, status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, lease_until TEXT,
          owner_id TEXT NOT NULL DEFAULT '', fencing_token INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS consolidation_job_key
          ON consolidation_job(scope_id, from_seq, to_seq, input_digest);
        CREATE TABLE IF NOT EXISTS event_candidate(
          event_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, event_type TEXT NOT NULL,
          title TEXT NOT NULL, state TEXT NOT NULL, current_revision INTEGER NOT NULL,
          narrator TEXT NOT NULL DEFAULT 'unknown', subject TEXT NOT NULL DEFAULT 'unknown',
          reality_status TEXT NOT NULL DEFAULT 'unknown', story_id TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event_revision(
          event_id TEXT NOT NULL, rev INTEGER NOT NULL, summary TEXT NOT NULL,
          status TEXT NOT NULL, modality TEXT NOT NULL, occurred_at TEXT,
          time_expression_raw TEXT, created_at TEXT NOT NULL,
          model_version TEXT NOT NULL DEFAULT '', prompt_version TEXT NOT NULL DEFAULT '',
          input_digest TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(event_id, rev),
          FOREIGN KEY(event_id) REFERENCES event_candidate(event_id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS event_evidence(
          event_id TEXT NOT NULL, rev INTEGER NOT NULL, scope_id TEXT NOT NULL,
          source_seq INTEGER NOT NULL, quote TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(event_id, rev, scope_id, source_seq, quote),
          FOREIGN KEY(event_id, rev) REFERENCES event_revision(event_id, rev) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS event_relation(
          id INTEGER PRIMARY KEY AUTOINCREMENT, scope_id TEXT NOT NULL,
          from_event TEXT NOT NULL, to_event TEXT NOT NULL, relation_type TEXT NOT NULL,
          status TEXT NOT NULL, source_seq INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event_link_proposal(
          proposal_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          candidate_event_id TEXT NOT NULL, label TEXT NOT NULL, reason_codes_json TEXT NOT NULL,
          model_version TEXT NOT NULL DEFAULT '', request_id TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event_link_decision(
          decision_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, scope_id TEXT NOT NULL,
          source_seq INTEGER NOT NULL, event_id TEXT, decision TEXT NOT NULL,
          reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
          UNIQUE(proposal_id, decision));
        CREATE TABLE IF NOT EXISTS event_cannot_link(
          id INTEGER PRIMARY KEY AUTOINCREMENT, scope_id TEXT NOT NULL,
          event_a TEXT NOT NULL, event_b TEXT NOT NULL, source_seq INTEGER,
          created_at TEXT NOT NULL, UNIQUE(scope_id, event_a, event_b));
        CREATE TABLE IF NOT EXISTS provider_usage(
          job_id TEXT NOT NULL, provider TEXT NOT NULL, tokens_in INTEGER NOT NULL DEFAULT 0,
          tokens_out INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS context_audit(
          request_id TEXT PRIMARY KEY, purpose TEXT NOT NULL, scope_id TEXT NOT NULL,
          policy_version TEXT NOT NULL, decision TEXT NOT NULL, source_refs_json TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS validation_issue(
          id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, op_index INTEGER NOT NULL,
          code TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gate_audit(
          audit_id TEXT PRIMARY KEY, request_id TEXT NOT NULL DEFAULT '', scope_id TEXT NOT NULL,
          source_seq INTEGER NOT NULL, gate_type TEXT NOT NULL, action TEXT NOT NULL,
          reason_codes TEXT NOT NULL, policy_version TEXT NOT NULL,
          candidate_event_id TEXT, relation_label TEXT, resolver_decision TEXT,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS resolution_pending(
          pending_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          proposals_json TEXT NOT NULL, mapping_json TEXT NOT NULL,
          reason TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS scope_lease(
          scope_id TEXT PRIMARY KEY, owner_id TEXT, fencing_token INTEGER NOT NULL DEFAULT 0,
          expires_at REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_receipt(
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, outcome TEXT NOT NULL,
          input_hash TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS source_manifest(
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, source_id TEXT NOT NULL,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS source_ready(
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          reason TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'READY', updated_at TEXT NOT NULL,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS dependency_version(
          scope_id TEXT NOT NULL, dep_type TEXT NOT NULL, dep_id TEXT NOT NULL,
          version INTEGER NOT NULL, content_hash TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL,
          PRIMARY KEY(scope_id, dep_type, dep_id));
        CREATE TABLE IF NOT EXISTS dependency_change(
          change_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, dep_type TEXT NOT NULL,
          dep_id TEXT NOT NULL, old_version INTEGER NOT NULL, new_version INTEGER NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_payload(
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, source_hash TEXT NOT NULL,
          recorded_at TEXT NOT NULL, text TEXT NOT NULL, provenance_json TEXT,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS memory_item(
          item_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, event_id TEXT NOT NULL,
          item_type TEXT NOT NULL, text TEXT NOT NULL, modality TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_tombstone(
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, deleted_at TEXT NOT NULL,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE IF NOT EXISTS revision_receipt(
          scope_id TEXT NOT NULL, old_seq INTEGER NOT NULL, new_seq INTEGER NOT NULL,
          input_hash TEXT NOT NULL, event_id TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(scope_id, old_seq, new_seq, input_hash));
        """)
        job_cols = {row[1] for row in self.db.execute("PRAGMA table_info(consolidation_job)")}
        if "owner_id" not in job_cols:
            self.db.execute("ALTER TABLE consolidation_job ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
        if "fencing_token" not in job_cols:
            self.db.execute("ALTER TABLE consolidation_job ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0")
        event_cols = {row[1] for row in self.db.execute("PRAGMA table_info(event_candidate)")}
        for column, ddl in (
            ("narrator", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("subject", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("reality_status", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("story_id", "TEXT"),
        ):
            if column not in event_cols:
                self.db.execute("ALTER TABLE event_candidate ADD COLUMN %s %s" % (column, ddl))
        payload_cols = {row[1] for row in self.db.execute("PRAGMA table_info(source_payload)")}
        if "provenance_json" not in payload_cols:
            self.db.execute("ALTER TABLE source_payload ADD COLUMN provenance_json TEXT")
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                        (str(SCHEMA_VERSION),))
        self.db.commit()

    @contextmanager
    def _write(self):
        """Commit only at the outer boundary; stay open inside a unit."""
        if self._unit_rollback_only:
            raise RuntimeError("unit is rollback-only")
        if self._unit_depth:
            yield
        else:
            with self.db:
                yield

    @contextmanager
    def unit_of_work(self, scope_id: str | None = None, scope_owner: str | None = None,
                     scope_token: int | None = None):
        """Rollback-only nested unit; only the outer boundary commits."""
        if self._unit_depth:
            self._unit_depth += 1
            try:
                yield self
            except Exception:
                self._unit_rollback_only = True
                raise
            finally:
                self._unit_depth -= 1
            return
        self.db.execute("BEGIN IMMEDIATE")
        self._unit_depth = 1
        self._unit_rollback_only = False
        try:
            if scope_id is not None and scope_owner is not None and scope_token is not None:
                if not self.verify_scope(scope_id, scope_owner, scope_token):
                    raise RuntimeError("scope lease fenced before unit")
            yield self
            if scope_id is not None and scope_owner is not None and scope_token is not None:
                if not self.verify_scope(scope_id, scope_owner, scope_token):
                    raise RuntimeError("scope lease fenced before commit")
        except Exception:
            self._unit_depth = 0
            self.db.rollback()
            raise
        else:
            if self._unit_rollback_only:
                self._unit_depth = 0
                self.db.rollback()
                raise RuntimeError("unit rollback-only: inner failure was swallowed")
            self._unit_depth = 0
            self.db.commit()
        finally:
            self._unit_depth = 0
            self._unit_rollback_only = False

    def integrity_check(self) -> str:
        return self.db.execute("PRAGMA integrity_check").fetchone()[0]

    def backup_to(self, path: str | Path) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        dest = sqlite3.connect(str(target))
        try:
            self.db.backup(dest)
            result = dest.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dest.close()
        return result

    def close(self) -> None:
        self.db.close()

    def cursor(self, scope_id: str) -> int:
        row = self.db.execute(
            "SELECT last_committed_seq FROM consolidation_cursor WHERE scope_id=?",
            (scope_id,)).fetchone()
        return int(row[0]) if row else 0

    def set_cursor(self, scope_id: str, seq: int) -> None:
        with self._write():
            self.db.execute("""INSERT INTO consolidation_cursor(scope_id,last_committed_seq,updated_at)
                VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET
                last_committed_seq=excluded.last_committed_seq, updated_at=excluded.updated_at""",
                (scope_id, seq, _now()))

    def candidates(self, scope_id: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.db.execute("""SELECT event_id,event_type,title,state,current_revision
            FROM event_candidate WHERE scope_id=? AND state!='withdrawn'
            ORDER BY updated_at DESC LIMIT ?""", (scope_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def record_job(self, scope_id: str, from_seq: int, to_seq: int, digest: str,
                   status: str, job_id: str | None = None) -> str:
        job_id = job_id or ("job_" + uuid.uuid4().hex)
        with self._write():
            self.db.execute("""INSERT OR IGNORE INTO consolidation_job
                (job_id,scope_id,from_seq,to_seq,input_digest,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, scope_id, from_seq, to_seq, digest, status, _now(), _now()))
        row = self.db.execute("""SELECT job_id FROM consolidation_job
            WHERE scope_id=? AND from_seq=? AND to_seq=? AND input_digest=?""",
            (scope_id, from_seq, to_seq, digest)).fetchone()
        return row[0] if row else job_id

    def job_status(self, job_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM consolidation_job WHERE job_id=?",
                              (job_id,)).fetchone()
        return row[0] if row else None

    def finish_job(self, job_id: str, status: str) -> None:
        with self._write():
            self.db.execute("UPDATE consolidation_job SET status=?,updated_at=? WHERE job_id=?",
                            (status, _now(), job_id))

    def claim_job(self, scope_id: str, from_seq: int, to_seq: int, digest: str,
                  owner_id: str, lease_seconds: int = 30) -> tuple[str, int] | None:
        """Claim a job or take over an expired lease; returns (job_id, token)."""
        now = datetime.now(timezone.utc)
        deadline = (now + timedelta(seconds=max(0, lease_seconds))).isoformat(timespec="seconds")
        with self._write():
            row = self.db.execute("""SELECT job_id,status,owner_id,lease_until,fencing_token
                FROM consolidation_job WHERE scope_id=? AND from_seq=? AND to_seq=? AND input_digest=?""",
                (scope_id, from_seq, to_seq, digest)).fetchone()
            if row is None:
                job_id = "job_" + uuid.uuid4().hex
                self.db.execute("""INSERT INTO consolidation_job
                    (job_id,scope_id,from_seq,to_seq,input_digest,status,attempts,lease_until,
                     owner_id,fencing_token,created_at,updated_at)
                    VALUES(?,?,?,?,?,'CLAIMED',1,?,?,1,?,?)""",
                    (job_id, scope_id, from_seq, to_seq, digest, deadline, owner_id,
                     _now(), _now()))
                return job_id, 1
            job_id, status, current_owner, lease_until, token = row
            if status in ("COMMITTED", "done"):
                return None
            if lease_until and lease_until > now.isoformat(timespec="seconds"):
                if current_owner == owner_id:
                    return None
                return None
            new_token = int(token) + 1
            self.db.execute("""UPDATE consolidation_job SET status='CLAIMED',owner_id=?,
                lease_until=?,fencing_token=?,attempts=attempts+1,updated_at=? WHERE job_id=?""",
                (owner_id, deadline, new_token, _now(), job_id))
            return job_id, new_token

    def renew_lease(self, job_id: str, owner_id: str, token: int, lease_seconds: int = 30) -> bool:
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=max(0, lease_seconds))
                    ).isoformat(timespec="seconds")
        with self._write():
            cur = self.db.execute("""UPDATE consolidation_job SET lease_until=?,updated_at=?
                WHERE job_id=? AND owner_id=? AND fencing_token=? AND status!='COMMITTED'""",
                (deadline, _now(), job_id, owner_id, int(token)))
            return cur.rowcount > 0

    def verify_lease(self, job_id: str, owner_id: str, token: int) -> bool:
        row = self.db.execute("""SELECT owner_id,fencing_token,lease_until,status
            FROM consolidation_job WHERE job_id=?""", (job_id,)).fetchone()
        if not row:
            return False
        owner, actual_token, lease_until, status = row
        return (owner == owner_id and int(actual_token) == int(token)
                and status not in ("COMMITTED", "done")
                and lease_until is not None
                and lease_until > _now())

    def commit_job(self, job_id: str, owner_id: str, token: int,
                   cursor_seq: int, scope_owner: str | None = None,
                   scope_token: int | None = None) -> bool:
        """Atomically commit the job marker and cursor; stale tokens are rejected."""
        with self._write():
            row = self.db.execute("""SELECT scope_id,owner_id,fencing_token,status,lease_until
                FROM consolidation_job WHERE job_id=?""", (job_id,)).fetchone()
            if not row:
                return False
            scope_id, owner, actual_token, status, lease_until = row
            if (owner != owner_id or int(actual_token) != int(token)
                    or status in ("COMMITTED", "done")):
                return False
            if not lease_until or lease_until <= _now():
                return False
            if scope_owner is not None and scope_token is not None:
                scope_row = self.db.execute("""SELECT 1 FROM scope_lease
                    WHERE scope_id=? AND owner_id=? AND fencing_token=?
                    AND expires_at > ?""",
                    (scope_id, scope_owner, scope_token, time.time())).fetchone()
                if scope_row is None:
                    return False
            self.db.execute("""INSERT INTO consolidation_cursor
                (scope_id,last_committed_seq,updated_at) VALUES(?,?,?)
                ON CONFLICT(scope_id) DO UPDATE SET
                last_committed_seq=excluded.last_committed_seq,updated_at=excluded.updated_at""",
                (scope_id, int(cursor_seq), _now()))
            self.db.execute("""UPDATE consolidation_job SET status='COMMITTED',lease_until=NULL,
                updated_at=? WHERE job_id=?""", (_now(), job_id))
            return True

    def fail_job(self, job_id: str, owner_id: str, token: int, retryable: bool = True) -> bool:
        status = "FAILED_RETRYABLE" if retryable else "FAILED"
        with self._write():
            cur = self.db.execute("""UPDATE consolidation_job SET status=?,lease_until=NULL,
                updated_at=? WHERE job_id=? AND owner_id=? AND fencing_token=?""",
                (status, _now(), job_id, owner_id, int(token)))
            return cur.rowcount > 0

    def source_ref_upsert(self, scope_id: str, seq: int, sha: str, recorded_at: str) -> None:
        with self._write():
            self.db.execute("""INSERT INTO source_ref(scope_id,source_seq,source_hash,recorded_at)
                VALUES(?,?,?,?) ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                source_hash=excluded.source_hash, recorded_at=excluded.recorded_at""",
                (scope_id, seq, sha, recorded_at))

    def sync_inventory(self, rows: Iterable[tuple[str, int, str]], run_id: str) -> int:
        """Reconcile full V1 key set; missing sources become tombstones."""
        rows = list(rows)
        with self._write():
            for scope, seq, sha in rows:
                self.db.execute("""INSERT INTO source_inventory
                    (scope_id,source_seq,source_hash,last_seen,deleted_at)
                    VALUES(?,?,?,?,NULL) ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                    source_hash=excluded.source_hash,last_seen=excluded.last_seen,deleted_at=NULL""",
                    (scope, seq, sha, run_id))
            missing = self.db.execute(
                "SELECT scope_id,source_seq FROM source_inventory WHERE last_seen<>? AND deleted_at IS NULL",
                (run_id,)).fetchall()
            for scope, seq in missing:
                self.db.execute("UPDATE source_inventory SET deleted_at=? WHERE scope_id=? AND source_seq=?",
                                (_now(), scope, seq))
                self.db.execute("UPDATE source_ref SET deleted_at=? WHERE scope_id=? AND source_seq=?",
                                (_now(), scope, seq))
                self.db.execute("UPDATE event_evidence SET valid=0 WHERE scope_id=? AND source_seq=?",
                                (scope, seq))
            # Events with no valid evidence become ambiguous.
            self.db.execute("""UPDATE event_candidate SET state='ambiguous',updated_at=?
                WHERE state!='withdrawn' AND event_id IN (
                  SELECT e.event_id FROM event_candidate e
                  LEFT JOIN event_evidence v ON v.event_id=e.event_id AND v.valid=1
                  GROUP BY e.event_id HAVING COUNT(v.event_id)=0)""", (_now(),))
        return len(missing)

    def commit(self, scope_id: str, sources: list[Any], operations: list[Any],
               job_id: str, model_version: str = "", prompt_version: str = "",
               input_digest: str = "") -> int:
        """Append accepted operations and advance the cursor in one transaction."""
        if not operations:
            self.finish_job(job_id, "deferred")
            return 0
        source_by_seq = {s.source_seq: s for s in sources}
        created = 0
        with self._write():
            for s in sources:
                self.db.execute("""INSERT INTO source_ref(scope_id,source_seq,source_hash,recorded_at)
                    VALUES(?,?,?,?) ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                    source_hash=excluded.source_hash, recorded_at=excluded.recorded_at""",
                    (scope_id, s.source_seq, s.source_hash, s.recorded_at))
            for op in operations:
                refs = []
                for ref in op.source_refs:
                    seq = ref.get("source_seq")
                    quote = str(ref.get("quote", ""))
                    if seq not in source_by_seq or not quote:
                        continue
                    if quote not in source_by_seq[seq].text:
                        continue
                    refs.append((seq, quote))
                if not refs:
                    continue
                if op.action == "create":
                    event_id = "evt_" + uuid.uuid4().hex
                    self.db.execute("""INSERT INTO event_candidate
                        (event_id,scope_id,event_type,title,state,current_revision,created_at,updated_at)
                        VALUES(?,?,?,?,'provisional',1,?,?)""",
                        (event_id, scope_id, op.event_type, op.title or op.summary[:80],
                         _now(), _now()))
                    rev = 1
                    created += 1
                else:
                    event_id = op.candidate_event_id or ""
                    row = self.db.execute(
                        "SELECT current_revision FROM event_candidate WHERE event_id=? AND scope_id=?",
                        (event_id, scope_id)).fetchone()
                    if not row:
                        continue
                    rev = int(row[0]) + 1
                    self.db.execute("""UPDATE event_candidate SET current_revision=?,updated_at=?
                        WHERE event_id=?""", (rev, _now(), event_id))
                self.db.execute("""INSERT INTO event_revision
                    (event_id,rev,summary,status,modality,occurred_at,time_expression_raw,
                     created_at,model_version,prompt_version,input_digest)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (event_id, rev, op.summary or op.title, op.status_proposal, op.modality,
                     op.occurred_at, op.time_expression_raw, _now(), model_version,
                     prompt_version, input_digest))
                for seq, quote in refs:
                    self.db.execute("""INSERT OR IGNORE INTO event_evidence
                        (event_id,rev,scope_id,source_seq,quote,valid)
                        VALUES(?,?,?,?,?,1)""", (event_id, rev, scope_id, seq, quote))
            last = max(s.source_seq for s in sources)
            self.db.execute("""INSERT INTO consolidation_cursor
                (scope_id,last_committed_seq,updated_at) VALUES(?,?,?)
                ON CONFLICT(scope_id) DO UPDATE SET
                last_committed_seq=excluded.last_committed_seq,updated_at=excluded.updated_at""",
                (scope_id, last, _now()))
            self.db.execute("UPDATE consolidation_job SET status='done',updated_at=? WHERE job_id=?",
                            (_now(), job_id))
        return created

    def create_event_from_source(self, scope_id: str, source: Any, event_type: str = "unknown",
                                 title: str = "", summary: str = "",
                                 event_id: str | None = None,
                                 provenance: dict | None = None) -> str:
        event_id = event_id or ("evt_" + uuid.uuid4().hex)
        provenance = provenance or {}
        now = _now()
        with self._write():
            self.db.execute("""INSERT INTO source_ref(scope_id,source_seq,source_hash,recorded_at)
                VALUES(?,?,?,?) ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                source_hash=excluded.source_hash,recorded_at=excluded.recorded_at""",
                (scope_id, source.source_seq, source.source_hash, source.recorded_at))
            self.db.execute("""INSERT INTO event_candidate
                (event_id,scope_id,event_type,title,state,current_revision,
                 narrator,subject,reality_status,story_id,created_at,updated_at)
                VALUES(?,?,?,?,'provisional',1,?,?,?,?,?,?)""",
                (event_id, scope_id, event_type, title or source.text[:80],
                 provenance.get("narrator", "unknown"),
                 provenance.get("subject", "unknown"),
                 provenance.get("reality_status", "unknown"),
                 provenance.get("story_id"), now, now))
            self.db.execute("""INSERT INTO event_revision
                (event_id,rev,summary,status,modality,created_at)
                VALUES(?,1,?,?,?,?)""",
                (event_id, summary or source.text[:200], "unknown", "unknown", now))
            self.db.execute("""INSERT OR IGNORE INTO event_evidence
                (event_id,rev,scope_id,source_seq,quote,valid) VALUES(?,1,?,?,?,1)""",
                (event_id, scope_id, source.source_seq, source.text))
        return event_id

    def record_usage(self, job_id: str, provider: str, tokens_in: int,
                     tokens_out: int, outcome: str) -> None:
        with self._write():
            self.db.execute("""INSERT INTO provider_usage
                (job_id,provider,tokens_in,tokens_out,outcome,created_at)
                VALUES(?,?,?,?,?,?)""", (job_id, provider, tokens_in, tokens_out, outcome, _now()))

    def record_validation_issues(self, job_id: str, rejected: list[tuple[Any, list[Any]]]) -> None:
        with self._write():
            for index, (_op, issues) in enumerate(rejected):
                for issue in issues:
                    self.db.execute("""INSERT INTO validation_issue
                        (job_id,op_index,code,detail,created_at) VALUES(?,?,?,?,?)""",
                        (job_id, index, issue.code, issue.detail[:200], _now()))

    def record_gate_audit(self, request_id: str, scope_id: str, source_seq: int,
                          gate_type: str, action: str, reason_codes: list[str],
                          policy_version: str, candidate_event_id: str | None = None,
                          relation_label: str | None = None,
                          resolver_decision: str | None = None) -> str:
        audit_id = "gate_" + uuid.uuid4().hex
        with self._write():
            self.db.execute("""INSERT INTO gate_audit
                (audit_id,request_id,scope_id,source_seq,gate_type,action,reason_codes,
                 policy_version,candidate_event_id,relation_label,resolver_decision,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (audit_id, request_id, scope_id, source_seq, gate_type, action,
                 json.dumps(reason_codes, ensure_ascii=False), policy_version,
                 candidate_event_id, relation_label, resolver_decision, _now()))
        return audit_id

    def save_pending(self, scope_id: str, source_seq: int, proposals: list[dict],
                     mapping: dict, reason: str, source: Any | None = None,
                     provenance: Any | None = None) -> str:
        now = _now()
        pending_id = "pending_" + uuid.uuid4().hex
        with self._write():
            if source is not None:
                self.db.execute("""INSERT OR REPLACE INTO source_payload
                    (scope_id,source_seq,source_hash,recorded_at,text,provenance_json)
                    VALUES(?,?,?,?,?,?)""",
                    (scope_id, source_seq, source.source_hash,
                     source.recorded_at, source.text,
                     json.dumps(provenance.as_dict(), ensure_ascii=False)
                     if provenance is not None else None))
            self.db.execute("""INSERT INTO resolution_pending
                (pending_id,scope_id,source_seq,proposals_json,mapping_json,
                 reason,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'PENDING',?,?)
                ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                proposals_json=excluded.proposals_json,
                mapping_json=excluded.mapping_json, reason=excluded.reason,
                status='PENDING', updated_at=excluded.updated_at""",
                (pending_id, scope_id, source_seq,
                 json.dumps(proposals, ensure_ascii=False, sort_keys=True),
                 json.dumps(mapping, ensure_ascii=False, sort_keys=True),
                 reason, now, now))
        return pending_id

    def pending_for_source(self, scope_id: str, source_seq: int) -> dict | None:
        row = self.db.execute("""SELECT pending_id,proposals_json,mapping_json,
            reason,status FROM resolution_pending WHERE scope_id=? AND source_seq=?""",
            (scope_id, source_seq)).fetchone()
        if not row:
            return None
        return {"pending_id": row[0], "proposals": json.loads(row[1]),
                "mapping": json.loads(row[2]), "reason": row[3], "status": row[4]}

    def pending_source(self, scope_id: str, source_seq: int) -> Any | None:
        row = self.db.execute("""SELECT source_hash,recorded_at,text FROM source_payload
            WHERE scope_id=? AND source_seq=?""", (scope_id, source_seq)).fetchone()
        if row is None:
            return None
        from .contracts import SourceEnvelope
        return SourceEnvelope(scope_id, source_seq, row[0], row[1], row[2])

    def pending_provenance(self, scope_id: str, source_seq: int) -> Any | None:
        row = self.db.execute("""SELECT provenance_json FROM source_payload
            WHERE scope_id=? AND source_seq=?""", (scope_id, source_seq)).fetchone()
        if not row or not row[0]:
            return None
        from .contracts import NarrativeProvenance
        return NarrativeProvenance(**json.loads(row[0]))

    def mark_pending_relocated(self, scope_id: str, source_seq: int,
                               event_id: str) -> bool:
        with self._write():
            cursor = self.db.execute("""UPDATE resolution_pending
                SET status='RELOCATED', updated_at=? WHERE scope_id=? AND source_seq=?
                AND status='PENDING'""", (_now(), scope_id, source_seq))
        return cursor.rowcount > 0

    def relocate_pending(self, scope_id: str, source_seq: int, source: Any | None,
                         event_id: str, source_provenance: Any | None = None) -> bool:
        """Attach a previously pending source and close its pending row atomically."""
        with self.unit_of_work():
            row = self.db.execute("""SELECT 1 FROM resolution_pending
                WHERE scope_id=? AND source_seq=? AND status='PENDING'""",
                (scope_id, source_seq)).fetchone()
            if row is None:
                return False
            if source is None:
                payload = self.db.execute("""SELECT source_hash,recorded_at,text
                    FROM source_payload WHERE scope_id=? AND source_seq=?""",
                    (scope_id, source_seq)).fetchone()
                if payload is None:
                    return False
                from .contracts import SourceEnvelope
                source = SourceEnvelope(scope_id, source_seq, payload[0], payload[1], payload[2])
            if source_provenance is None:
                source_provenance = self.pending_provenance(scope_id, source_seq)
            from .provenance_guard import ProvenanceGuard
            from .contracts import NarrativeProvenance
            if source_provenance is None:
                return False
            check = ProvenanceGuard().check(
                source_provenance,
                NarrativeProvenance(**self.event_provenance(event_id)))
            if check.action != "ALLOW":
                self.record_gate_audit(
                    "pending-" + str(source_seq), scope_id, source_seq,
                    "PendingRelocation", check.action, list(check.reason_codes),
                    check.policy_version, event_id, "SAME_EVENT", "PROVENANCE_BLOCKED")
                return False
            self.invalidate_source_evidence(scope_id, source_seq)
            self.attach_source(scope_id, source, event_id, "pending-relocation")
            self.mark_pending_relocated(scope_id, source_seq, event_id)
            self.record_gate_audit(
                "pending-" + str(source_seq), scope_id, source_seq,
                "PendingRelocation", "ALLOW", ["dependency_resolved"],
                "pending-relocation-1", event_id, "SAME_EVENT", "ATTACHED")
        return True

    def auto_relocate_pending(self, scope_id: str) -> dict:
        """Relocate only when the stored mapping union has one target."""
        relocated = 0
        withheld = 0
        rows = self.db.execute("""SELECT source_seq,mapping_json FROM resolution_pending
            WHERE scope_id=? AND status='PENDING' ORDER BY source_seq""",
            (scope_id,)).fetchall()
        for seq, mapping_json in rows:
            targets = set()
            for values in json.loads(mapping_json).values():
                targets.update(values)
            if len(targets) == 1 and self.relocate_pending(
                    scope_id, seq, None, next(iter(targets)),
                    source_provenance=self.pending_provenance(scope_id, seq)):
                relocated += 1
            else:
                withheld += 1
        return {"relocated": relocated, "withheld": withheld}

    def claim_scope(self, scope_id: str, owner_id: str, ttl: float = 30.0) -> int | None:
        now = time.time()
        last_error = None
        for _attempt in range(5):
            try:
                with self._write():
                    self.db.execute("BEGIN IMMEDIATE")
                    row = self.db.execute("SELECT owner_id,fencing_token,expires_at FROM scope_lease WHERE scope_id=?",
                                          (scope_id,)).fetchone()
                    if row and row["owner_id"] and row["expires_at"] > now:
                        return None
                    token = (row["fencing_token"] + 1) if row else 1
                    self.db.execute("""INSERT INTO scope_lease
                        (scope_id,owner_id,fencing_token,expires_at,updated_at)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(scope_id) DO UPDATE SET owner_id=excluded.owner_id,
                        fencing_token=excluded.fencing_token, expires_at=excluded.expires_at,
                        updated_at=excluded.updated_at""",
                        (scope_id, owner_id, token, now + ttl, _now()))
                return token
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(0.05)
        raise last_error

    def verify_scope(self, scope_id: str, owner_id: str, token: int) -> bool:
        row = self.db.execute("""SELECT 1 FROM scope_lease WHERE scope_id=? AND owner_id=?
            AND fencing_token=? AND expires_at > ?""",
            (scope_id, owner_id, token, time.time())).fetchone()
        return row is not None

    def renew_scope(self, scope_id: str, owner_id: str, token: int, ttl: float = 30.0) -> bool:
        with self._write():
            cursor = self.db.execute("""UPDATE scope_lease SET expires_at=?, updated_at=?
                WHERE scope_id=? AND owner_id=? AND fencing_token=? AND expires_at > ?""",
                (time.time() + ttl, _now(), scope_id, owner_id, token, time.time()))
        return cursor.rowcount > 0

    def release_scope(self, scope_id: str, owner_id: str, token: int) -> bool:
        with self._write():
            cursor = self.db.execute("""UPDATE scope_lease
                SET owner_id=NULL, expires_at=0, updated_at=?
                WHERE scope_id=? AND owner_id=? AND fencing_token=?""",
                (_now(), scope_id, owner_id, token))
        return cursor.rowcount > 0

    def revision_result(self, scope_id: str, old_seq: int, new_seq: int,
                        input_hash: str) -> str | None:
        row = self.db.execute("""SELECT event_id FROM revision_receipt
            WHERE scope_id=? AND old_seq=? AND new_seq=? AND input_hash=?""",
            (scope_id, old_seq, new_seq, input_hash)).fetchone()
        return row[0] if row else None

    def save_revision_result(self, scope_id: str, old_seq: int, new_seq: int,
                             input_hash: str, event_id: str) -> None:
        with self._write():
            self.db.execute("""INSERT OR IGNORE INTO revision_receipt
                (scope_id,old_seq,new_seq,input_hash,event_id,created_at)
                VALUES(?,?,?,?,?,?)""",
                (scope_id, old_seq, new_seq, input_hash, event_id, _now()))

    def receipt_for_source(self, scope_id: str, source_seq: int) -> dict | None:
        row = self.db.execute("""SELECT outcome,input_hash FROM source_receipt
            WHERE scope_id=? AND source_seq=?""", (scope_id, source_seq)).fetchone()
        if row is None:
            return None
        return {"outcome": row[0], "input_hash": row[1]}

    def record_receipt(self, scope_id: str, source_seq: int, outcome: str,
                       input_hash: str) -> bool:
        with self._write():
            row = self.db.execute("""SELECT input_hash FROM source_receipt
                WHERE scope_id=? AND source_seq=?""", (scope_id, source_seq)).fetchone()
            if row:
                return row[0] == input_hash
            self.db.execute("""INSERT INTO source_receipt
                (scope_id,source_seq,outcome,input_hash,created_at) VALUES(?,?,?,?,?)""",
                (scope_id, source_seq, outcome, input_hash, _now()))
        return True

    def update_dependency(self, scope_id: str, dep_type: str, dep_id: str,
                          new_version: int, affected_seqs: list[int],
                          reason: str, content_hash: str = "") -> bool:
        """Version bump + change log + READY enqueue in one transaction."""
        with self.unit_of_work():
            row = self.db.execute("""SELECT version FROM dependency_version
                WHERE scope_id=? AND dep_type=? AND dep_id=?""",
                (scope_id, dep_type, dep_id)).fetchone()
            old_version = row[0] if row else 0
            if new_version <= old_version:
                return False
            self.db.execute("""INSERT INTO dependency_version
                (scope_id,dep_type,dep_id,version,content_hash,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(scope_id,dep_type,dep_id) DO UPDATE SET
                version=excluded.version,content_hash=excluded.content_hash,
                updated_at=excluded.updated_at""",
                (scope_id, dep_type, dep_id, new_version, content_hash, _now()))
            self.db.execute("""INSERT INTO dependency_change
                (change_id,scope_id,dep_type,dep_id,old_version,new_version,
                 reason,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                ("dep_" + uuid.uuid4().hex, scope_id, dep_type, dep_id,
                 old_version, new_version, reason, _now()))
            for seq in affected_seqs:
                self.enqueue_ready(scope_id, seq, "dependency:" + dep_type)
        return True

    def enqueue_ready(self, scope_id: str, source_seq: int, reason: str,
                      generation: int = 1) -> None:
        with self._write():
            self.db.execute("""INSERT INTO source_ready
                (scope_id,source_seq,reason,generation,status,updated_at)
                VALUES(?,?,?,?,'READY',?)
                ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                reason=excluded.reason,generation=excluded.generation,
                status='READY',updated_at=excluded.updated_at""",
                (scope_id, source_seq, reason, generation, _now()))

    def poll_ready(self, scope_id: str, limit: int = 100) -> list[dict]:
        rows = self.db.execute("""SELECT source_seq,reason,generation FROM source_ready
            WHERE scope_id=? AND status='READY' ORDER BY source_seq LIMIT ?""",
            (scope_id, limit)).fetchall()
        return [{"source_seq": row[0], "reason": row[1], "generation": row[2]}
                for row in rows]

    def mark_ready_done(self, scope_id: str, source_seq: int,
                        expected_generation: int | None = None) -> bool:
        with self._write():
            if expected_generation is None:
                cursor = self.db.execute("""UPDATE source_ready SET status='DONE', updated_at=?
                    WHERE scope_id=? AND source_seq=? AND status='READY'""",
                    (_now(), scope_id, source_seq))
            else:
                cursor = self.db.execute("""UPDATE source_ready SET status='DONE', updated_at=?
                    WHERE scope_id=? AND source_seq=? AND status='READY'
                    AND generation=?""",
                    (_now(), scope_id, source_seq, expected_generation))
        return cursor.rowcount > 0

    def reconcile_ready(self, scope_id: str) -> dict:
        """Enqueue recoverable work without attaching or changing evidence."""
        missing = self.manifest_missing_receipts(scope_id)
        pending = self.db.execute("""SELECT source_seq,reason FROM resolution_pending
            WHERE scope_id=? AND status='PENDING' ORDER BY source_seq""",
            (scope_id,)).fetchall()
        for item in missing:
            self.enqueue_ready(scope_id, item["source_seq"], "missing_receipt")
        for row in pending:
            self.enqueue_ready(scope_id, row[0], "pending_recheck")
        return {"missing_receipts": len(missing), "pending": len(pending),
                "ready": len(self.poll_ready(scope_id, 100000))}

    def register_manifest(self, scope_id: str, entries: list[tuple[int, str]]) -> None:
        with self._write():
            for seq, source_id in entries:
                self.db.execute("""INSERT OR IGNORE INTO source_manifest
                    (scope_id,source_seq,source_id) VALUES(?,?,?)""",
                    (scope_id, seq, source_id))

    def manifest_missing_receipts(self, scope_id: str) -> list[dict]:
        rows = self.db.execute("""SELECT m.source_seq,m.source_id
            FROM source_manifest m LEFT JOIN source_receipt r
            ON m.scope_id=r.scope_id AND m.source_seq=r.source_seq
            WHERE m.scope_id=? AND r.source_seq IS NULL ORDER BY m.source_seq""",
            (scope_id,)).fetchall()
        return [{"source_seq": row[0], "source_id": row[1]} for row in rows]

    def contiguous_frontier(self, scope_id: str, start: int = 1) -> int:
        manifest = self.db.execute("""SELECT source_seq FROM source_manifest
            WHERE scope_id=? ORDER BY source_seq""", (scope_id,)).fetchall()
        if manifest:
            last = 0
            for row in manifest:
                receipt = self.db.execute("""SELECT 1 FROM source_receipt
                    WHERE scope_id=? AND source_seq=?""", (scope_id, row[0])).fetchone()
                if receipt is None:
                    break
                last = row[0]
            return last
        rows = self.db.execute("""SELECT source_seq FROM source_receipt
            WHERE scope_id=? ORDER BY source_seq""", (scope_id,)).fetchall()
        expected = start
        for row in rows:
            if row[0] != expected:
                break
            expected += 1
        return expected - 1

    def events_for_source(self, scope_id: str, source_seq: int) -> list[str]:
        rows = self.db.execute("""SELECT DISTINCT e.event_id FROM event_evidence e
            JOIN event_candidate c ON c.event_id=e.event_id
            WHERE e.scope_id=? AND e.source_seq=? AND e.valid=1 AND c.scope_id=?
            ORDER BY e.event_id""", (scope_id, source_seq, scope_id)).fetchall()
        return [row[0] for row in rows]

    def effective_events_for_source(self, scope_id: str, source_seq: int) -> list[str]:
        pending = self.db.execute("""SELECT 1 FROM resolution_pending
            WHERE scope_id=? AND source_seq=? AND status='PENDING'""",
            (scope_id, source_seq)).fetchone()
        if pending is not None:
            return []
        rows = self.db.execute("""SELECT DISTINCT e.event_id FROM event_evidence e
            JOIN event_candidate c ON c.event_id=e.event_id
            WHERE e.scope_id=? AND e.source_seq=? AND e.valid=1 AND c.scope_id=?
            AND c.state!='withdrawn' ORDER BY e.event_id""",
            (scope_id, source_seq, scope_id)).fetchall()
        return [row[0] for row in rows]

    def invalidate_source_evidence(self, scope_id: str, source_seq: int) -> int:
        with self._write():
            cursor = self.db.execute("""UPDATE event_evidence SET valid=0
                WHERE scope_id=? AND source_seq=? AND valid=1""",
                (scope_id, source_seq))
        return cursor.rowcount

    def delete_source(self, scope_id: str, source_seq: int) -> int:
        with self.unit_of_work():
            count = self.invalidate_source_evidence(scope_id, source_seq)
            now = _now()
            self.db.execute("""INSERT OR REPLACE INTO source_tombstone
                (scope_id,source_seq,deleted_at) VALUES(?,?,?)""",
                (scope_id, source_seq, now))
            self.db.execute("""UPDATE source_ref SET deleted_at=?
                WHERE scope_id=? AND source_seq=?""", (now, scope_id, source_seq))
        return count

    def withdraw_event(self, scope_id: str, event_id: str) -> bool:
        with self.unit_of_work():
            cursor = self.db.execute("""UPDATE event_candidate SET state='withdrawn',
                updated_at=? WHERE event_id=? AND scope_id=? AND state!='withdrawn'""",
                (_now(), event_id, scope_id))
            if cursor.rowcount:
                self.db.execute("""UPDATE event_evidence SET valid=0
                    WHERE scope_id=? AND event_id=? AND valid=1""",
                    (scope_id, event_id))
        return cursor.rowcount > 0

    def apply_tombstones(self, target: "V15Store") -> int:
        """Re-apply local source deletions after restoring a backup."""
        rows = self.db.execute("""SELECT scope_id,source_seq,deleted_at
            FROM source_tombstone""").fetchall()
        for scope_id, source_seq, deleted_at in rows:
            target.invalidate_source_evidence(scope_id, source_seq)
            with target._write():
                target.db.execute("""INSERT OR REPLACE INTO source_tombstone
                    (scope_id,source_seq,deleted_at) VALUES(?,?,?)""",
                    (scope_id, source_seq, deleted_at))
                target.db.execute("""UPDATE source_ref SET deleted_at=?
                    WHERE scope_id=? AND source_seq=?""",
                    (deleted_at, scope_id, source_seq))
        return len(rows)

    def event_in_scope(self, event_id: str, scope_id: str) -> bool:
        row = self.db.execute("""SELECT 1 FROM event_candidate
            WHERE event_id=? AND scope_id=? AND state!='withdrawn'""",
            (event_id, scope_id)).fetchone()
        return row is not None

    def save_memory_item(self, scope_id: str, event_id: str, item_type: str,
                         text: str, modality: str = "asserted") -> str:
        item_id = "mem_" + uuid.uuid4().hex
        with self._write():
            self.db.execute("""INSERT INTO memory_item
                (item_id,scope_id,event_id,item_type,text,modality,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (item_id, scope_id, event_id, item_type, text, modality, _now()))
        return item_id

    def memory_items_for_event(self, event_id: str) -> list[dict]:
        rows = self.db.execute("""SELECT item_id,item_type,text,modality
            FROM memory_item WHERE event_id=? ORDER BY created_at""", (event_id,)).fetchall()
        return [{"item_id": r[0], "item_type": r[1], "text": r[2], "modality": r[3]}
                for r in rows]

    def event_provenance(self, event_id: str) -> dict:
        row = self.db.execute("""SELECT narrator,subject,reality_status,story_id
            FROM event_candidate WHERE event_id=?""", (event_id,)).fetchone()
        if not row:
            return {"narrator": "unknown", "subject": "unknown",
                    "reality_status": "unknown", "story_id": None}
        return {"narrator": row[0], "subject": row[1],
                "reality_status": row[2], "story_id": row[3]}

    def cannot_link_exists(self, scope_id: str, event_a: str, event_b: str) -> bool:
        a, b = sorted((event_a, event_b))
        row = self.db.execute("""SELECT 1 FROM event_cannot_link
            WHERE scope_id=? AND event_a=? AND event_b=?""", (scope_id, a, b)).fetchone()
        return row is not None

    def add_cannot_link(self, scope_id: str, event_a: str, event_b: str,
                        source_seq: int) -> None:
        if event_a == event_b:
            return
        a, b = sorted((event_a, event_b))
        with self._write():
            self.db.execute("""INSERT OR IGNORE INTO event_cannot_link
                (scope_id,event_a,event_b,source_seq,created_at) VALUES(?,?,?,?,?)""",
                (scope_id, a, b, source_seq, _now()))

    def record_link_proposal(self, scope_id: str, source_seq: int, candidate_event_id: str,
                             label: str, reason_codes: list[str], model_version: str,
                             request_id: str) -> str:
        proposal_id = "prop_" + uuid.uuid4().hex
        with self._write():
            self.db.execute("""INSERT INTO event_link_proposal
                (proposal_id,scope_id,source_seq,candidate_event_id,label,reason_codes_json,
                 model_version,request_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (proposal_id, scope_id, source_seq, candidate_event_id, label,
                 json.dumps(reason_codes, ensure_ascii=False), model_version, request_id, _now()))
        return proposal_id

    def record_link_decision(self, proposal_id: str, scope_id: str, source_seq: int,
                             event_id: str | None, decision: str, reason: str = "") -> str:
        decision_id = "dec_" + uuid.uuid4().hex
        with self._write():
            self.db.execute("""INSERT OR IGNORE INTO event_link_decision
                (decision_id,proposal_id,scope_id,source_seq,event_id,decision,reason,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (decision_id, proposal_id, scope_id, source_seq, event_id, decision,
                 reason[:300], _now()))
        row = self.db.execute("""SELECT decision_id FROM event_link_decision
            WHERE proposal_id=? AND decision=?""", (proposal_id, decision)).fetchone()
        return row[0] if row else decision_id

    def attach_source(self, scope_id: str, source: Any, event_id: str,
                      proposal_id: str) -> bool:
        """Attach a source to an existing event; append-only evidence/decision."""
        row = self.db.execute("""SELECT current_revision FROM event_candidate
            WHERE event_id=? AND scope_id=? AND state!='withdrawn'""",
            (event_id, scope_id)).fetchone()
        if not row:
            return False
        revision = int(row[0])
        with self._write():
            self.db.execute("""INSERT INTO source_ref(scope_id,source_seq,source_hash,recorded_at)
                VALUES(?,?,?,?) ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                source_hash=excluded.source_hash,recorded_at=excluded.recorded_at""",
                (scope_id, source.source_seq, source.source_hash, source.recorded_at))
            before = self.db.total_changes
            self.db.execute("""INSERT OR IGNORE INTO event_evidence
                (event_id,rev,scope_id,source_seq,quote,valid) VALUES(?,?,?,?,?,1)""",
                (event_id, revision, scope_id, source.source_seq, source.text))
            inserted = self.db.total_changes > before
            self.db.execute("UPDATE event_candidate SET updated_at=? WHERE event_id=?",
                            (_now(), event_id))
            self.db.execute("""INSERT OR IGNORE INTO event_link_decision
                (decision_id,proposal_id,scope_id,source_seq,event_id,decision,reason,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                ("dec_" + uuid.uuid4().hex, proposal_id, scope_id, source.source_seq,
                 event_id, "ATTACHED" if inserted else "ALREADY_ATTACHED", "", _now()))
        return inserted

    def audit(self, request_id: str, purpose: str, scope_id: str, policy_version: str,
              decision: str, refs: list[dict[str, Any]]) -> None:
        with self._write():
            self.db.execute("""INSERT INTO context_audit
                (request_id,purpose,scope_id,policy_version,decision,source_refs_json,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (request_id, purpose, scope_id, policy_version, decision,
                 json.dumps(refs, ensure_ascii=False), _now()))

    def status(self) -> dict[str, Any]:
        def count(table: str) -> int:
            return int(self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
        return {"source_refs": count("source_ref"), "events": count("event_candidate"),
                "revisions": count("event_revision"), "evidence": count("event_evidence"),
                "validation_issues": count("validation_issue"),
                "tombstones": int(self.db.execute(
                    "SELECT COUNT(*) FROM source_inventory WHERE deleted_at IS NOT NULL").fetchone()[0])}
