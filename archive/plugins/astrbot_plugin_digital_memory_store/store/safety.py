"""Data-safety guards for the V1 memory store.

This module intentionally does not know about claims, events, or V2. It only
protects the existing SQLite file: one writer at a time, a schema fence, and a
pre-migration copy.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import suppress
from pathlib import Path

SCHEMA_VERSION = 19
APP_VERSION = "1.0.19"


class StoreSafetyError(RuntimeError):
    """Base class for refusal-to-open conditions."""


class StoreLockedError(StoreSafetyError):
    """Another process (or another Store instance) owns the data directory."""


class SchemaTooNewError(StoreSafetyError):
    """The database was written by a newer schema than this code understands."""


class StoreCorruptError(StoreSafetyError):
    """SQLite quick_check did not return ok."""


class StoreLock:
    """Cross-platform non-blocking exclusive lock on the data directory.

    The lock is held for the lifetime of Store. The lock file is not deleted;
    the OS lock is released automatically if the process exits.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            payload = json.dumps({
                "pid": os.getpid(),
                "started_at": int(time.time()),
                "app": APP_VERSION,
            }, ensure_ascii=False).encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, payload)
            os.ftruncate(fd, len(payload))
            self._fd = fd
        except OSError as exc:
            with suppress(OSError):
                os.close(fd)
            owner = self._owner()
            detail = f"（当前持有者：{owner}）" if owner else ""
            raise StoreLockedError(
                "记忆数据库已被另一个实例占用" + detail
            ) from exc

    def _owner(self) -> str:
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace").strip()
            if not raw:
                return ""
            data = json.loads(raw)
            return "pid=%s app=%s" % (data.get("pid", "?"), data.get("app", "?"))
        except (OSError, ValueError):
            return ""

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                with suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                with suppress(OSError):
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            with suppress(OSError):
                os.close(fd)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)


def inspect_existing(path: Path) -> int | None:
    """Return the stored schema version, or None for a legacy database.

    Raises StoreCorruptError if quick_check fails. Does not create the file.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    conn = _connect_readonly(path)
    try:
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise StoreCorruptError("记忆数据库无法读取：%s" % exc) from exc
        if not row or row[0] != "ok":
            raise StoreCorruptError("记忆数据库 quick_check 未通过：%r" % (row,))
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dl_meta'"
        ).fetchone()
        if not exists:
            return None
        row = conn.execute(
            "SELECT value FROM dl_meta WHERE key='schema_version'"
        ).fetchone()
        if not row:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            raise StoreCorruptError("记忆数据库 schema_version 非法")
    finally:
        conn.close()


def backup_before_migration(path: Path, backup_dir: Path) -> Path:
    """Make a consistent copy before any schema write. Returns the copy path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    target = backup_dir / ("memory_%s_%s.sqlite3" % (stamp, os.urandom(4).hex()))
    source = _connect_readonly(path)
    dest = sqlite3.connect(str(target))
    try:
        source.backup(dest)
        dest.commit()
        check = dest.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise StoreCorruptError("升级前备份 quick_check 未通过")
    finally:
        dest.close()
        source.close()
    return target
