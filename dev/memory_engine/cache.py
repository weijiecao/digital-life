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
