"""双数据库一致备份和原子活动代次指针；不原地覆盖运行库。"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from contextlib import contextmanager
from astrbot_memory_input.native import digest, Origin, Record


@contextmanager
def managed_database(*args, **kwargs):
    """短期连接明确管理事务和句柄，不把连接关闭留给垃圾回收。

    sqlite3.Connection 的 with 协议只负责提交/回滚；Windows 下遗漏 close
    会使备份重命名和临时目录清理失败。finally 同时覆盖查询与事务异常。
    """
    connection = sqlite3.connect(*args, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(source, target):
    # SQLite backup 包含 WAL 已提交内容；不直接复制活动 DB 文件。
    with managed_database(Path(source).resolve().as_uri() + "?mode=ro", uri=True) as src:
        with managed_database(str(target)) as dst:
            src.backup(dst)


def validate_database(path, required):
    with managed_database(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("database integrity failed")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("foreign key check failed")
        tables = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not set(required) <= tables:
            raise ValueError("unsupported database schema")
        if "source_ref" in required:
            version = db.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if version != ("1",):
                raise ValueError("unsupported business schema version")


class GenerationStore:
    def __init__(self, root, recover=True):
        self.root = Path(root)
        self.pointer = self.root / "CURRENT.json"
        self.operation = self.root / "restore_operation.json"
        self.current = read_json(self.pointer, {"directory": ".", "epoch": 1})
        self.directory(self.current["directory"])
        if type(self.current.get("epoch")) is not int or self.current["epoch"] < 1:
            raise ValueError("invalid generation")
        if recover:
            self.recover()

    def directory(self, name):
        if name != "." and not re.fullmatch(r"generations/[a-f0-9]{32}", name):
            raise ValueError("invalid generation path")
        target = (self.root / name).resolve()
        if name != "." and not target.is_relative_to(self.root.resolve()):
            raise ValueError("generation escapes root")
        return target

    @property
    def active(self):
        return self.directory(self.current["directory"])

    def recover(self):
        op = read_json(self.operation)
        if op and op["phase"] not in ("COMMITTED", "ROLLED_BACK"):
            # SWITCH 后崩溃而未通过验证：启动时整组回退，保留候选目录供诊断。
            self.current = {
                "directory": op["old"]["directory"],
                "epoch": max(self.current["epoch"], op["new"]["epoch"]) + 1,
            }
            atomic_json(self.pointer, self.current)
            op["phase"] = "ROLLED_BACK"
            atomic_json(self.operation, op)

    def backups(self):
        result = []
        for file in sorted((self.root / "backups").glob("*/manifest.json")):
            try:
                value = read_json(file)
                result.append(
                    {
                        "id": file.parent.name,
                        "generation": value["generation"],
                        "files": len(value["files"]),
                    }
                )
            except (OSError, ValueError, KeyError):
                continue
        return result

    def create_backup(self, config):
        identity = uuid.uuid4().hex
        target = self.root / "backups" / identity
        target.mkdir(parents=True)
        for name in ("shadow.sqlite3", "native_input_journal.sqlite3"):
            snapshot(self.active / name, target / name)
        state = self.active / "commit_state.json"
        if state.exists():
            shutil.copyfile(state, target / state.name)
        # 迁移冻结源也是备份集的一部分，原分组/摘要可追溯且不会升格为事实。
        if (self.active / "migrations").exists():
            shutil.copytree(self.active / "migrations", target / "migrations")
        files = {
            f.relative_to(target).as_posix(): checksum(f)
            for f in target.rglob("*")
            if f.is_file()
        }
        manifest = {
            "format": "digital-life-backup-v1",
            "generation": self.current["epoch"],
            "files": files,
            "config": config,
        }
        atomic_json(target / "manifest.json", manifest)
        self.validate_backup(identity)
        return {
            "id": identity,
            "files": len(files),
            "generation": manifest["generation"],
        }

    def validate_backup(self, identity):
        if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
            raise ValueError("invalid backup id")
        base = (self.root / "backups" / identity).resolve()
        if not base.is_relative_to(self.root.resolve()):
            raise ValueError("backup escapes root")
        manifest = read_json(base / "manifest.json")
        if not manifest or manifest.get("format") != "digital-life-backup-v1":
            raise ValueError("invalid backup manifest")
        files = manifest.get("files", {})
        if not {"shadow.sqlite3", "native_input_journal.sqlite3"} <= set(files):
            raise ValueError("incomplete backup set")
        for name, sha in files.items():
            if name not in (
                "shadow.sqlite3",
                "native_input_journal.sqlite3",
                "commit_state.json",
            ) and not re.fullmatch(r"migrations/[a-f0-9]{64}\.sqlite3", name):
                raise ValueError("unexpected backup path")
            file = (base / name).resolve()
            if not file.is_relative_to(base) or checksum(file) != sha:
                raise ValueError("backup hash mismatch")
        validate_database(
            base / "shadow.sqlite3",
            ("native_input_inbox", "source_ref", "event_candidate"),
        )
        validate_database(
            base / "native_input_journal.sqlite3", ("native_record", "native_stream")
        )
        with managed_database(
            (base / "shadow.sqlite3").as_uri() + "?mode=ro", uri=True
        ) as target:
            with managed_database(
                (base / "native_input_journal.sqlite3").as_uri() + "?mode=ro", uri=True
            ) as journal:
                tables = {
                    r[0]
                    for r in journal.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                table = (
                    "input_canonical"
                    if "input_canonical" in tables
                    else "native_record"
                )
                for key, payload in journal.execute(
                    "SELECT key,payload FROM " + table + " WHERE delivered=1"
                ):
                    record = json.loads(payload)
                    normalized = Record(**record)
                    origin = Origin(**json.loads(normalized.origin_json))
                    if (
                        digest(json.loads(normalized.raw_json))
                        != normalized.content_hash
                        or origin.scope_id != normalized.scope_id
                        or origin.stream_id != normalized.stream_id
                    ):
                        raise ValueError("source payload hash or identity mismatch")
                    row = target.execute(
                        "SELECT content_hash,scope_id FROM native_input_inbox WHERE key=?",
                        (key,),
                    ).fetchone()
                    if row is None or row != (
                        record["content_hash"],
                        record["scope_id"],
                    ):
                        raise ValueError(
                            "journal and business database receipt mismatch"
                        )
                for key, content_hash, scope, payload in target.execute(
                    "SELECT key,content_hash,scope_id,payload FROM native_input_inbox"
                ):
                    row = journal.execute(
                        "SELECT payload FROM " + table + " WHERE key=?", (key,)
                    ).fetchone()
                    record = json.loads(row[0]) if row else {}
                    if record != json.loads(payload):
                        raise ValueError("business and journal source payload mismatch")
                    if (record.get("content_hash"), record.get("scope_id")) != (
                        content_hash,
                        scope,
                    ):
                        raise ValueError(
                            "business database and journal receipt mismatch"
                        )
            if target.execute(
                "SELECT 1 FROM sqlite_master WHERE name='migration_runs'"
            ).fetchone():
                for name, sha in target.execute(
                    "SELECT snapshot,sha FROM migration_runs"
                ):
                    if files.get("migrations/" + name) != sha:
                        raise ValueError("missing migration snapshot")
        return manifest

    def import_shadow(self, source):
        """管理员确认停用旧实例后，整组冻结导入为可校验的备份，不重建事件。"""
        source = Path(source).resolve()
        identity = uuid.uuid4().hex
        target = self.root / "backups" / identity
        target.mkdir(parents=True)
        for name in ("shadow.sqlite3", "native_input_journal.sqlite3"):
            snapshot(source / name, target / name)
        if (source / "commit_state.json").exists():
            shutil.copyfile(source / "commit_state.json", target / "commit_state.json")
        if (source / "migrations").exists():
            shutil.copytree(source / "migrations", target / "migrations")
        files = {
            f.relative_to(target).as_posix(): checksum(f)
            for f in target.rglob("*")
            if f.is_file()
        }
        atomic_json(
            target / "manifest.json",
            {
                "format": "digital-life-backup-v1",
                "generation": 0,
                "files": files,
                "config": {},
                "origin": "legacy-shadow",
            },
        )
        self.validate_backup(identity)
        return {"id": identity, "verified": True, "next": "restore"}

    def prepare_restore(self, identity):
        manifest = self.validate_backup(identity)
        name = "generations/" + uuid.uuid4().hex
        target = self.directory(name)
        target.mkdir(parents=True)
        for file in manifest["files"]:
            dest = target / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / "backups" / identity / file, dest)
            if checksum(dest) != manifest["files"][file]:
                raise ValueError("staging copy mismatch")
        op = {
            "id": uuid.uuid4().hex,
            "backup": identity,
            "phase": "PREPARED",
            "old": dict(self.current),
            "new": {"directory": name, "epoch": self.current["epoch"] + 1},
        }
        atomic_json(self.operation, op)
        return op

    def switch(self, op):
        self.current = dict(op["new"])
        atomic_json(self.pointer, self.current)
        op["phase"] = "SWITCHED"
        atomic_json(self.operation, op)

    def finish(self, op):
        op["phase"] = "COMMITTED"
        atomic_json(self.operation, op)

    def rollback(self, op):
        self.current = {
            "directory": op["old"]["directory"],
            "epoch": self.current["epoch"] + 1,
        }
        atomic_json(self.pointer, self.current)
        op["phase"] = "ROLLED_BACK"
        atomic_json(self.operation, op)
