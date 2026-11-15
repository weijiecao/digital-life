"""旧基座冻结快照、显式身份映射与断点迁移；旧摘要只保留作历史产物。"""

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from astrbot_memory_input.native import Origin, Record, canonical, digest
from .storage import snapshot, checksum, managed_database
from astrbot_memory_input.journal import ImportConflict


class LegacyMigration:
    def __init__(self, store, service, directory):
        self.store, self.service, self.directory = store, service, Path(directory)
        store.db.executescript("""
        CREATE TABLE IF NOT EXISTS migration_runs(
          id TEXT PRIMARY KEY,snapshot TEXT NOT NULL,sha TEXT NOT NULL,mapping TEXT NOT NULL,
          state TEXT NOT NULL,watermark INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL,error TEXT,inventory TEXT NOT NULL DEFAULT '{}',source_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS migration_items(
          run TEXT NOT NULL,item TEXT NOT NULL,raw TEXT NOT NULL,target_key TEXT,status TEXT NOT NULL,
          PRIMARY KEY(run,item));
        """)

    @staticmethod
    def _tables(db):
        tables = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "dl_capture" not in tables:
            raise ValueError("not a supported legacy memory base")
        return tables

    # LEFT JOIN 让 SQLite 可建立临时索引，避免逐条扫描 capture 判断已关联来源。
    _messages_sql = """FROM messages m LEFT JOIN dl_capture c
        ON c.scope=m.scope AND c.linked_source=m.id WHERE c.seq IS NULL"""

    @classmethod
    def inventory(cls, db):
        tables = cls._tables(db)
        scopes = dict(
            db.execute("SELECT scope,COUNT(*) FROM dl_capture GROUP BY scope")
        )
        if "messages" in tables:
            for scope, count in db.execute(
                "SELECT m.scope,COUNT(*) " + cls._messages_sql + " GROUP BY m.scope"
            ):
                scopes[scope] = scopes.get(scope, 0) + count
        return scopes

    @classmethod
    def rows(cls, db, start, limit):
        """只加载当前批次，水位仍以冻结快照中的确定顺序定义。"""
        tables = cls._tables(db)
        captures = db.execute("SELECT COUNT(*) FROM dl_capture").fetchone()[0]
        result = []
        if start < captures:
            for row in db.execute(
                "SELECT * FROM dl_capture ORDER BY seq LIMIT ? OFFSET ?",
                (min(limit, captures - start), start),
            ):
                r = dict(row)
                result.append(
                    (
                        "capture:" + str(r["seq"]),
                        r["scope"],
                        r["content"],
                        r.get("recorded_at", ""),
                        "user",
                        r.get("state") in ("ignored", "deleted", "withdrawn")
                        or bool(r.get("deleted_at")),
                        r,
                    )
                )
        if len(result) < limit and "messages" in tables:
            for row in db.execute(
                "SELECT m.* "
                + cls._messages_sql
                + " ORDER BY m.scope,m.id LIMIT ? OFFSET ?",
                (limit - len(result), max(0, start - captures)),
            ):
                r = dict(row)
                result.append(
                    (
                        "message:" + r["scope"] + ":" + str(r["id"]),
                        r["scope"],
                        r["content"],
                        r.get("recorded_at", ""),
                        r.get("speaker", "assistant"),
                        False,
                        r,
                    )
                )
        return result

    def create(self, source, mapping, source_id=None):
        source = Path(source).resolve()
        # 同一个旧库的后续快照沿用来源身份；搬家时显式传入先前的 source_id。
        source_id = source_id or hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("invalid source_id")
        if source == self.store.path.resolve():
            raise ValueError("cannot migrate running target")
        folder = self.directory / "migrations"
        folder.mkdir(parents=True, exist_ok=True)
        import uuid

        temp = folder / (uuid.uuid4().hex + ".tmp")
        try:
            snapshot(source, temp)
            sha = checksum(temp)
            dest = folder / (sha + ".sqlite3")
            if not dest.exists():
                temp.replace(dest)
            else:
                temp.unlink()
            with managed_database(str(dest)) as db:
                db.row_factory = sqlite3.Row
                scopes = self.inventory(db)
            if not isinstance(mapping, dict):
                raise ValueError("mapping must be a legacy scope to Origin object")
            normalized = {
                str(scope): asdict(Origin(**value)) for scope, value in mapping.items()
            }
            run = digest([source_id, sha])
            prior = self.store.db.execute(
                "SELECT mapping FROM migration_runs WHERE id=?", (run,)
            ).fetchone()
            if prior and json.loads(prior[0]) != normalized:
                raise ValueError("use mapping update for quarantined scopes")
            for (old_mapping,) in self.store.db.execute(
                "SELECT mapping FROM migration_runs WHERE source_id=?", (source_id,)
            ):
                for scope, value in json.loads(old_mapping).items():
                    if scope in normalized and normalized[scope] != value:
                        raise ValueError(
                            "source identity mapping cannot change across snapshots"
                        )
            with self.store.unit_of_work():
                self.store.db.execute(
                    "INSERT OR IGNORE INTO migration_runs(id,snapshot,sha,mapping,state,total,inventory,source_id) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        run,
                        dest.name,
                        sha,
                        canonical(normalized),
                        "SNAPSHOTTED",
                        sum(scopes.values()),
                        canonical(scopes),
                        source_id,
                    ),
                )
            return self.status(run)
        finally:
            if temp.exists():
                temp.unlink()

    def update_mapping(self, run, mapping):
        row = self._run(run)
        current = json.loads(row["mapping"])
        for (prior,) in self.store.db.execute(
            "SELECT mapping FROM migration_runs WHERE source_id=?", (row["source_id"],)
        ):
            for scope, value in json.loads(prior).items():
                if scope in mapping and asdict(Origin(**mapping[scope])) != value:
                    raise ValueError(
                        "source identity mapping cannot change across snapshots"
                    )
        for scope, value in mapping.items():
            origin = asdict(Origin(**value))
            if scope in current and current[scope] != origin:
                raise ValueError("an existing identity mapping is immutable")
            current[scope] = origin
        with self.store.unit_of_work():
            self.store.db.execute(
                "UPDATE migration_runs SET mapping=?,watermark=0,state='SNAPSHOTTED',error=NULL WHERE id=?",
                (canonical(current), run),
            )
        return self.status(run)

    def _run(self, run):
        row = self.store.db.execute(
            "SELECT * FROM migration_runs WHERE id=?", (run,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown migration run")
        return dict(row)

    def batch(self, run, limit=200):
        self._run(run)
        try:
            return self._batch(run, limit)
        except Exception as exc:
            with self.store.unit_of_work():
                self.store.db.execute(
                    "UPDATE migration_runs SET state='FAILED',error=? WHERE id=?",
                    (type(exc).__name__ + ": " + str(exc), run),
                )
            raise

    def _batch(self, run, limit=200):
        row = self._run(run)
        file = self.directory / "migrations" / row["snapshot"]
        if checksum(file) != row["sha"]:
            raise ValueError("migration snapshot hash changed")
        start = row["watermark"]
        limit = max(1, min(int(limit), 1000))
        with managed_database(str(file)) as db:
            db.row_factory = sqlite3.Row
            rows = self.rows(db, start, limit)
        mapping = json.loads(row["mapping"])
        end = start + len(rows)
        for index, item in enumerate(rows, start=start):
            identity, scope, text, stamp, role, ignored, raw = item
            prior = self.store.db.execute(
                "SELECT status FROM migration_items WHERE run=? AND item=?",
                (run, identity),
            ).fetchone()
            if prior and prior[0] in ("IMPORTED", "ARCHIVED", "RELEASED", "DUPLICATE"):
                with self.store.unit_of_work():
                    self.store.db.execute(
                        "UPDATE migration_runs SET watermark=? WHERE id=?",
                        (index + 1, run),
                    )
                continue
            key = None
            record = None
            status = "QUARANTINED"
            valid_hash = (
                not raw.get("content_sha")
                or hashlib.sha256(text.encode("utf-8")).hexdigest()
                == raw["content_sha"]
            )
            if not valid_hash:
                status = "INVALID_HASH"
            if scope in mapping and valid_hash:
                origin = Origin(**mapping[scope])
                message = {
                    "role": role if role in ("user", "assistant") else "assistant",
                    "message_id": "legacy:" + row["source_id"] + ":" + identity,
                    "content": text,
                    "timestamp": stamp,
                    "legacy": raw,
                    "migration_hold": True,
                }
                if ignored:
                    message["source_kind"] = "legacy_archive"
                try:
                    staged = self.service.journal.append(message, origin)
                    key = staged["key"]
                    if key is None:
                        raise ValueError("migration identity unexpectedly ambiguous")
                    record = self.service.journal._canonical(key)
                    status = "ARCHIVED" if ignored or role != "user" else "IMPORTED"
                except ImportConflict:
                    status = "IDENTITY_CONFLICT"
            # 业务收件箱与迁移回执同事务；journal 的确认在此事务提交之后才更新。
            with self.store.unit_of_work():
                if record:
                    if self.store.db.execute(
                        "SELECT 1 FROM native_input_inbox WHERE key=?", (key,)
                    ).fetchone():
                        status = "DUPLICATE"
                    self.service.inbox.accept(record)
                self.store.db.execute(
                    "INSERT OR REPLACE INTO migration_items VALUES(?,?,?,?,?)",
                    (run, identity, canonical(raw), key, status),
                )
                self.store.db.execute(
                    "UPDATE migration_runs SET watermark=?,state='IMPORTING',error=NULL WHERE id=?",
                    (index + 1, run),
                )
        self.service.deliver_pending()
        with self.store.unit_of_work():
            self.store.db.execute(
                "UPDATE migration_runs SET state=? WHERE id=?",
                ("READY" if end == row["total"] else "IMPORTING", run),
            )
        return self.status(run)

    def release(self, run):
        row = self._run(run)
        if row["state"] != "READY":
            raise ValueError("finish migration batches before organizing")
        with self.store.unit_of_work():
            count = self.store.db.execute(
                """UPDATE native_input_inbox SET state='QUEUED'
              WHERE state='MIGRATION_HELD' AND key IN
              (SELECT target_key FROM migration_items WHERE run=? AND status IN ('IMPORTED','DUPLICATE'))""",
                (run,),
            ).rowcount
            self.store.db.execute(
                "UPDATE migration_items SET status='RELEASED' WHERE run=? AND status IN ('IMPORTED','DUPLICATE')",
                (run,),
            )
        return {"released": count, **self.status(run)}

    def status(self, run=None):
        if run is None:
            return [
                self.status(r[0])
                for r in self.store.db.execute(
                    "SELECT id FROM migration_runs ORDER BY rowid"
                )
            ]
        row = self._run(run)
        counts = {
            r[0]: r[1]
            for r in self.store.db.execute(
                "SELECT status,COUNT(*) FROM migration_items WHERE run=? GROUP BY status",
                (run,),
            )
        }
        return {
            "id": run,
            "state": row["state"],
            "watermark": row["watermark"],
            "total": row["total"],
            "source_id": row["source_id"],
            "source_scopes": json.loads(row["inventory"]),
            "counts": counts,
            "remaining": row["total"] - sum(counts.values()),
            "error": row["error"],
        }
