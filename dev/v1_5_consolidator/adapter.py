"""Read-only adapter for the V1 SQLite source.

It never writes the V1 database and never imports the V1 plugin package.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .contracts import SourceEnvelope


class V1ReadOnlyAdapter:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError("V1 数据库不存在：%s" % self.db_path)
        self.db = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")
        self._check_schema()

    def _check_schema(self) -> None:
        cols = {row[1] for row in self.db.execute("PRAGMA table_info(dl_capture)")}
        required = {"seq", "scope", "content", "content_sha", "recorded_at", "state"}
        missing = required - cols
        if missing:
            raise ValueError("V1 dl_capture 缺少字段：%s" % ",".join(sorted(missing)))

    def close(self) -> None:
        self.db.close()

    def scopes(self) -> list[str]:
        return [row[0] for row in self.db.execute(
            "SELECT DISTINCT scope FROM dl_capture ORDER BY scope")]

    def read_after(self, scope_id: str, after_seq: int, limit: int = 40) -> list[SourceEnvelope]:
        rows = self.db.execute("""SELECT seq,scope,content,content_sha,recorded_at,state
            FROM dl_capture WHERE scope=? AND seq>? ORDER BY seq ASC LIMIT ?""",
            (scope_id, int(after_seq), int(limit))).fetchall()
        return [SourceEnvelope(scope_id=row["scope"], source_seq=int(row["seq"]),
                               source_hash=row["content_sha"], recorded_at=row["recorded_at"],
                               text=row["content"], state=row["state"]) for row in rows]

    def source_keys(self) -> list[tuple[str, int, str]]:
        return [(row["scope"], int(row["seq"]), row["content_sha"]) for row in self.db.execute(
            "SELECT scope,seq,content_sha FROM dl_capture ORDER BY scope,seq")]
