"""V1.5 independent SQLite store.

The store only holds derived candidates/evidence/jobs. It never opens the V1
database for writing and never creates V1 formal events.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
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
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS consolidation_job_key
          ON consolidation_job(scope_id, from_seq, to_seq, input_digest);
        CREATE TABLE IF NOT EXISTS event_candidate(
          event_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, event_type TEXT NOT NULL,
          title TEXT NOT NULL, state TEXT NOT NULL, current_revision INTEGER NOT NULL,
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
        """)
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                        (str(SCHEMA_VERSION),))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def cursor(self, scope_id: str) -> int:
        row = self.db.execute(
            "SELECT last_committed_seq FROM consolidation_cursor WHERE scope_id=?",
            (scope_id,)).fetchone()
        return int(row[0]) if row else 0

    def set_cursor(self, scope_id: str, seq: int) -> None:
        with self.db:
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
        with self.db:
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
        with self.db:
            self.db.execute("UPDATE consolidation_job SET status=?,updated_at=? WHERE job_id=?",
                            (status, _now(), job_id))

    def source_ref_upsert(self, scope_id: str, seq: int, sha: str, recorded_at: str) -> None:
        with self.db:
            self.db.execute("""INSERT INTO source_ref(scope_id,source_seq,source_hash,recorded_at)
                VALUES(?,?,?,?) ON CONFLICT(scope_id,source_seq) DO UPDATE SET
                source_hash=excluded.source_hash, recorded_at=excluded.recorded_at""",
                (scope_id, seq, sha, recorded_at))

    def sync_inventory(self, rows: Iterable[tuple[str, int, str]], run_id: str) -> int:
        """Reconcile full V1 key set; missing sources become tombstones."""
        rows = list(rows)
        with self.db:
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
        with self.db:
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

    def record_usage(self, job_id: str, provider: str, tokens_in: int,
                     tokens_out: int, outcome: str) -> None:
        with self.db:
            self.db.execute("""INSERT INTO provider_usage
                (job_id,provider,tokens_in,tokens_out,outcome,created_at)
                VALUES(?,?,?,?,?,?)""", (job_id, provider, tokens_in, tokens_out, outcome, _now()))

    def record_validation_issues(self, job_id: str, rejected: list[tuple[Any, list[Any]]]) -> None:
        with self.db:
            for index, (_op, issues) in enumerate(rejected):
                for issue in issues:
                    self.db.execute("""INSERT INTO validation_issue
                        (job_id,op_index,code,detail,created_at) VALUES(?,?,?,?,?)""",
                        (job_id, index, issue.code, issue.detail[:200], _now()))

    def audit(self, request_id: str, purpose: str, scope_id: str, policy_version: str,
              decision: str, refs: list[dict[str, Any]]) -> None:
        with self.db:
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
