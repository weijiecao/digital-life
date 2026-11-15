"""实时追加与历史快照共用的持久身份账本；不把文本相同当成同一消息。"""

import json
from .journal import ImportJournal, ImportConflict
from .native import Record, canonical, parse_history, digest


def equivalent(left, right):
    # 不同宿主入口的时间/包装字段可以不同；正文、角色和摘要属性必须一致。
    a, b = json.loads(left.raw_json), json.loads(right.raw_json)
    return all(
        a.get(k, default) == b.get(k, default)
        for k, default in [
            ("role", None),
            ("content", None),
            ("source_kind", "message"),
            ("is_summary", False),
        ]
    )


def fingerprint(record):
    # 此索引只识别“可能重叠”以暂缓，不授权合并。兼容纯文本与文本块包装差异。
    raw = json.loads(record.raw_json)
    return digest(
        [
            record.role,
            record.text.strip(),
            raw.get("source_kind", "message"),
            raw.get("is_summary", False),
        ]
    )


class UnifiedJournal(ImportJournal):
    def __init__(self, path):
        super().__init__(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS input_canonical(
          key TEXT PRIMARY KEY,payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS input_alias(
          alias TEXT PRIMARY KEY,stream TEXT NOT NULL,kind TEXT NOT NULL,
          payload TEXT NOT NULL,canonical_key TEXT,reason TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS input_alias_stream ON input_alias(stream,kind);
        CREATE TABLE IF NOT EXISTS input_overlap(
          alias TEXT PRIMARY KEY, stream TEXT NOT NULL, kind TEXT NOT NULL,
          fingerprint TEXT NOT NULL, positional INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS input_overlap_find ON input_overlap(stream,kind,fingerprint,positional);
        CREATE TABLE IF NOT EXISTS input_tombstone(key TEXT PRIMARY KEY,reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS unified_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        if not self.db.execute(
            "SELECT 1 FROM unified_meta WHERE key='bootstrap'"
        ).fetchone():
            # 接续旧 journal：重放仍由旧 key 幂等，不能把已处理历史再次判别。
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for key, stream, payload, delivered in self.db.execute(
                    "SELECT key,stream_id,payload,delivered FROM native_record"
                ).fetchall():
                    self.db.execute(
                        "INSERT OR IGNORE INTO input_canonical VALUES(?,?,?)",
                        (key, payload, delivered),
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO input_alias VALUES(?,?,?,?,?,?)",
                        ("history:" + key, stream, "history", payload, key, ""),
                    )
                self.db.execute("INSERT INTO unified_meta VALUES('bootstrap','1')")
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

        if not self.db.execute(
            "SELECT 1 FROM unified_meta WHERE key='overlap_index'"
        ).fetchone():
            with self.db:
                for alias, stream, kind, payload in self.db.execute(
                    "SELECT alias,stream,kind,payload FROM input_alias"
                ).fetchall():
                    record = Record(**json.loads(payload))
                    self.db.execute(
                        "INSERT OR REPLACE INTO input_overlap VALUES(?,?,?,?,?)",
                        (
                            alias,
                            stream,
                            kind,
                            fingerprint(record),
                            int(record.native_id is None),
                        ),
                    )
                self.db.execute("INSERT INTO unified_meta VALUES('overlap_index','1')")

    def _has_overlap(self, record, kind, positional=False):
        sql = "SELECT 1 FROM input_overlap WHERE stream=? AND kind=? AND fingerprint=?"
        if positional:
            sql += " AND positional=1"
        return (
            self.db.execute(
                sql + " LIMIT 1", (record.stream_id, kind, fingerprint(record))
            ).fetchone()
            is not None
        )

    def _canonical(self, key):
        row = self.db.execute(
            "SELECT payload FROM input_canonical WHERE key=?", (key,)
        ).fetchone()
        return Record(**json.loads(row[0])) if row else None

    def _put(self, record, kind, linked_key=None, hold=False):
        alias = kind + ":" + record.key
        old = self.db.execute(
            "SELECT payload,canonical_key FROM input_alias WHERE alias=?", (alias,)
        ).fetchone()
        if old and json.loads(old[0])["content_hash"] != record.content_hash:
            raise ImportConflict("source identity content changed")
        if old and old[1]:
            return {"key": old[1], "new": 0, "pending": 0}
        key = linked_key or record.key
        existing = self._canonical(key)
        if existing and not equivalent(record, existing):
            raise ImportConflict("cross-entry identity content changed")
        if hold and existing is None:
            key = None
        added = 0
        if key and existing is None:
            added = self.db.execute(
                "INSERT OR IGNORE INTO input_canonical(key,payload) VALUES(?,?)",
                (key, canonical(record.as_dict())),
            ).rowcount
        self.db.execute(
            "INSERT OR REPLACE INTO input_alias VALUES(?,?,?,?,?,?)",
            (
                alias,
                record.stream_id,
                kind,
                canonical(record.as_dict()),
                key,
                "" if key else "ambiguous_overlap",
            ),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO input_overlap VALUES(?,?,?,?,?)",
            (
                alias,
                record.stream_id,
                kind,
                fingerprint(record),
                int(record.native_id is None),
            ),
        )
        return {"key": key, "new": added, "pending": int(key is None)}

    def append(self, message, origin):
        """稳定消息 ID 追加；同文不同 ID 不合并。历史身份不明时持久保留待定位。"""
        records = parse_history([message], origin)
        if len(records) != 1:
            raise ImportConflict("realtime append requires one source message")
        record = records[0]
        if record.native_id is None:
            raise ImportConflict("realtime append requires stable message_id")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            ambiguous = self._has_overlap(record, "history", positional=True)
            result = self._put(record, "realtime", hold=ambiguous)
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    def stage(self, payload, origin, links=None):
        """保留原快照前缀规则。links 仅接受可信适配器提供的 ordinal→平台 ID。"""
        staged = super().stage(payload, origin)
        records = parse_history(payload, origin)
        links = links or {}
        if len(set(str(v) for v in links.values())) != len(links):
            raise ImportConflict(
                "one realtime message cannot bind two history positions"
            )
        result = {"new": 0, "duplicates": 0, "pending": 0}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for record in records:
                linked = None
                identity = links.get(str(record.ordinal))
                if identity is not None:
                    alias = "realtime:" + digest(
                        [origin.stream_id, "native_id", str(identity)]
                    )
                    row = self.db.execute(
                        "SELECT payload FROM input_alias WHERE alias=?", (alias,)
                    ).fetchone()
                    match = [Record(**json.loads(row[0]))] if row else []
                    if len(match) != 1 or not equivalent(record, match[0]):
                        raise ImportConflict("invalid trusted history link")
                    target = self._canonical(match[0].key)
                    if target is None:
                        # 之前历史先到：已有的 positional canonical 是权威身份。
                        target = self._canonical(record.key)
                        if target is None:
                            self._put(match[0], "realtime")
                            target = self._canonical(match[0].key)
                    linked = target.key
                    self.db.execute(
                        "UPDATE input_alias SET canonical_key=?,reason='' WHERE alias=?",
                        (linked, "realtime:" + match[0].key),
                    )
                ambiguous = record.native_id is None and self._has_overlap(
                    record, "realtime"
                )
                item = self._put(
                    record, "history", linked, hold=ambiguous and linked is None
                )
                result["new"] += item["new"]
                result["pending"] += item["pending"]
                result["duplicates"] += int(not item["new"] and not item["pending"])
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return result

    def deliver(self, sink, limit=100):
        sent = 0
        rows = self.db.execute(
            """SELECT key,payload FROM input_canonical
          WHERE delivered=0 AND key NOT IN (SELECT key FROM input_tombstone)
          ORDER BY rowid LIMIT ?""",
            (max(0, limit),),
        ).fetchall()
        for key, payload in rows:
            record = Record(**json.loads(payload))
            if sink.accept(record) != {"key": key, "content_hash": record.content_hash}:
                raise ImportConflict("sink acknowledgement mismatch")
            self.db.execute(
                "UPDATE input_canonical SET delivered=1 WHERE key=?", (key,)
            )
            sent += 1
        remaining = self.db.execute(
            """SELECT COUNT(*) FROM input_canonical WHERE delivered=0
          AND key NOT IN (SELECT key FROM input_tombstone)"""
        ).fetchone()[0]
        return {"acknowledged": sent, "remaining": remaining}

    def pending(self):
        return self.db.execute(
            "SELECT COUNT(*) FROM input_alias WHERE canonical_key IS NULL"
        ).fetchone()[0]

    def tombstone(self, key, reason):
        self.db.execute(
            "INSERT OR IGNORE INTO input_tombstone VALUES(?,?)", (key, reason)
        )
