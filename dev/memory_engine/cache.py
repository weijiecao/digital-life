"""Small generation-keyed context cache for the M5 slice."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class ContextCache:
    def __init__(self):
        self._data: dict[tuple, tuple[int, Any]] = {}

    def get(self, key: tuple, generation: int):
        row = self._data.get(key)
        if row is None or row[0] != generation:
            return None
        return row[1]

    def put(self, key: tuple, generation: int, value: Any) -> None:
        self._data[key] = (generation, value)

    def invalidate_scope(self, scope_id: str) -> None:
        for key in list(self._data):
            if key and key[0] == scope_id:
                del self._data[key]

    def size(self) -> int:
        return len(self._data)


class PersistentContextCache:
    """Context cache shared by engine instances through the same SQLite DB."""

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _key(key: tuple) -> str:
        return json.dumps(key, ensure_ascii=False, sort_keys=True)

    def get(self, key: tuple, generation: int):
        row = self.store.db.execute(
            "SELECT packet_json FROM context_cache WHERE cache_key=?",
            (self._key(key),)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: tuple, generation: int, value: Any) -> None:
        from datetime import datetime, timezone
        for attempt in range(20):
            try:
                with self.store._write():
                    self.store.db.execute(
                        """INSERT OR REPLACE INTO context_cache
                           (cache_key,generation,packet_json,updated_at) VALUES(?,?,?,?)""",
                        (self._key(key), generation,
                         json.dumps(value, ensure_ascii=False),
                         datetime.now(timezone.utc).isoformat()))
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 19:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def invalidate_scope(self, scope_id: str) -> None:
        with self.store._write():
            self.store.db.execute("DELETE FROM context_cache WHERE cache_key LIKE ?",
                                  ('["' + scope_id + '"%',))

    def size(self) -> int:
        return self.store.db.execute("SELECT COUNT(*) FROM context_cache").fetchone()[0]

    def prune(self, max_rows: int = 1000, older_than_seconds: float | None = None) -> int:
        removed = 0
        with self.store._write():
            if older_than_seconds is not None:
                from datetime import datetime, timedelta, timezone
                cutoff = (datetime.now(timezone.utc) - timedelta(
                    seconds=older_than_seconds)).isoformat()
                cursor = self.store.db.execute(
                    "DELETE FROM context_cache WHERE updated_at < ?", (cutoff,))
                removed += cursor.rowcount
            cursor = self.store.db.execute(
                """DELETE FROM context_cache WHERE cache_key NOT IN (
                   SELECT cache_key FROM context_cache
                   ORDER BY updated_at DESC LIMIT ?)""", (max(0, max_rows),))
            removed += cursor.rowcount
        return removed


class CacheJanitor:
    """Optional periodic TTL/size cleanup for the persistent cache."""

    def __init__(self, cache: PersistentContextCache,
                 interval_seconds: float = 3600, max_rows: int = 1000,
                 ttl_seconds: float = 86400, state_path=None,
                 lock_ttl_seconds: float = 3600):
        import json
        from pathlib import Path
        self.cache = cache
        self.interval_seconds = interval_seconds
        self.max_rows = max_rows
        self.ttl_seconds = ttl_seconds
        self.state_path = Path(state_path) if state_path else None
        self.lock_ttl_seconds = lock_ttl_seconds
        self.last_run: float | None = None
        if self.state_path and self.state_path.exists():
            try:
                self.last_run = json.loads(
                    self.state_path.read_text(encoding="utf-8")).get("last_run")
            except (OSError, ValueError):
                self.last_run = None

    def maybe_run(self, now: float | None = None) -> int | None:
        import json
        import os
        import time
        now = time.time() if now is None else now
        if self.last_run is not None and now - self.last_run < self.interval_seconds:
            return None
        lock_path = (self.state_path.with_suffix(".lock")
                     if self.state_path else None)
        fd = None
        if lock_path:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > self.lock_ttl_seconds:
                        lock_path.unlink()
                        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except (FileNotFoundError, FileExistsError, OSError):
                    pass
                if fd is None:
                    return None
        try:
            removed = self.cache.prune(
                self.max_rows, older_than_seconds=self.ttl_seconds)
            self.last_run = now
            if self.state_path:
                self.state_path.write_text(
                    json.dumps({"last_run": now}), encoding="utf-8")
            return removed
        finally:
            if fd is not None:
                os.close(fd)
                lock_path.unlink(missing_ok=True)
