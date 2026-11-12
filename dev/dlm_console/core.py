"""控制台的查询与维护逻辑（不依赖 AstrBot、记忆引擎的运行时）。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from . import APP_NAME, __version__
from .config import Paths, describe

READ_PAGE_MAX = 200
AUDIT_LIMIT = 500

STORE_STATE_LABELS = {
    "pending": "待审",
    "organized": "已整理",
    "ignored": "已忽略",
    "deleted": "已删除",
}

# 备份必须是应用能直接读取的库：只有表名存在不代表结构可用。
# 这里显式列出恢复后主链路会读写的关键列与主键，缺一即判定备份无效。
REQUIRED_SCHEMA = {
    "store": {
        "dl_capture": ("seq", "scope", "event_hash", "content", "content_sha",
                       "recorded_at", "state"),
    },
    "engine": {
        "event_candidate": ("event_id", "scope_id", "event_type", "title",
                            "state", "current_revision"),
        "event_revision": ("event_id", "rev", "summary", "status", "modality",
                           "created_at"),
        "event_evidence": ("event_id", "rev", "scope_id", "source_seq", "quote",
                           "valid"),
    },
    "journal": {
        "native_record": ("key", "stream_id", "ordinal", "content_hash",
                          "payload", "delivered"),
        "native_stream": ("stream_id", "mode"),
    },
}
REQUIRED_PRIMARY_KEYS = {
    "store": {"dl_capture": ("seq",)},
    "engine": {"event_candidate": ("event_id",)},
    "journal": {"native_record": ("key",)},
}


class ApiError(RuntimeError):
    """带 HTTP 状态码的、可以直接展示给操作者的错误。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# 基础工具


def _ro_uri(path: Path) -> str:
    # Windows 的盘符冒号必须保留；中文和空格需要百分号编码。
    return "file:" + quote(Path(path).resolve().as_posix(), safe="/:") + "?mode=ro"


@contextmanager
def open_ro(path: Path):
    if not Path(path).is_file():
        raise ApiError("数据库不存在: %s" % path, 404)
    con = sqlite3.connect(_ro_uri(Path(path)), uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=3000")
        yield con
    finally:
        con.close()


@contextmanager
def open_backup(path: Path):
    """以不可变单文件方式打开备份，避免校验时生成 -wal/-shm。"""
    if not Path(path).is_file():
        raise ApiError("备份文件不存在: %s" % path, 404)
    uri = "file:" + quote(Path(path).resolve().as_posix(), safe="/:") + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        yield con
    finally:
        con.close()


@contextmanager
def open_rw(path: Path):
    if not Path(path).is_file():
        raise ApiError("数据库不存在: %s" % path, 404)
    con = sqlite3.connect(str(path), timeout=10)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=8000")
        yield con
    finally:
        con.close()


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,)).fetchone() is not None


def scalar(con: sqlite3.Connection, sql: str, params=()):
    try:
        row = con.execute(sql, params).fetchone()
    except sqlite3.DatabaseError:
        return None
    return row[0] if row else None


def _json(text, default=None):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _file_info(path: Path | None) -> dict:
    if path is None:
        return {"path": None, "exists": False, "bytes": 0, "mtime": None}
    try:
        stat = Path(path).stat()
        return {"path": str(path), "exists": True, "bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")}
    except OSError:
        return {"path": str(path), "exists": False, "bytes": 0, "mtime": None}


def _iso(ts) -> str | None:
    if ts in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _tail_jsonl(path: Path, limit: int = 5000,
                max_bytes: int = 8 * 1024 * 1024) -> tuple[list[dict], bool]:
    """读取 JSONL 尾部；返回 (行列表, 是否只读到部分文件)。"""
    if path is None or not Path(path).is_file():
        return [], False
    size = Path(path).stat().st_size
    with Path(path).open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            data = handle.read()
        else:
            data = handle.read()
    lines = data.decode("utf-8", "replace").splitlines()
    truncated = size > max_bytes
    if truncated and lines:
        lines = lines[1:]  # 丢掉可能被截断的首行
    rows = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        item = _json(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows, truncated


def _sql_page(con: sqlite3.Connection, select: str, count: str, params: tuple,
              page, size) -> dict:
    size = _clamp(size, 1, READ_PAGE_MAX, 20)
    page = max(1, _clamp(page, 1, 10 ** 6, 1))
    total = con.execute(count, params).fetchone()[0]
    rows = con.execute(select + " LIMIT ? OFFSET ?",
                       (*params, size, (page - 1) * size)).fetchall()
    return {"items": [dict(row) for row in rows], "total": total,
            "page": page, "size": size,
            "pages": max(1, (total + size - 1) // size)}


def _list_page(items: list, page, size) -> dict:
    size = _clamp(size, 1, READ_PAGE_MAX, 20)
    page = max(1, _clamp(page, 1, 10 ** 6, 1))
    total = len(items)
    return {"items": items[(page - 1) * size: page * size], "total": total,
            "page": page, "size": size,
            "pages": max(1, (total + size - 1) // size)}


def _mask_config(config: dict) -> dict:
    """配置在页面展示时遮掉可能包含身份或密钥的值。"""
    masked = {}
    sensitive = ("id", "key", "token", "secret", "password", "cookie", "dir")
    for key, value in config.items():
        if any(word in key.lower() for word in sensitive):
            if isinstance(value, str) and value.strip():
                masked[key] = "已配置（%d 字符）" % len(value)
            elif isinstance(value, list):
                masked[key] = "已配置（%d 项）" % len(value)
            else:
                masked[key] = value
        else:
            masked[key] = value
    return masked


# ---------------------------------------------------------------------------
# 控制台核心


class Console:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.read_only = paths.read_only
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._audit_lock = threading.Lock()
        self._audit_path = paths.workspace / ".dlm_console" / "audit.jsonl"

    # -- 数据集 ---------------------------------------------------------

    def dataset_specs(self) -> list[dict]:
        specs = [
            {"key": "store", "label": "数字记忆基座",
             "db": self.paths.store_db,
             "dir": self.paths.store_dir,
             "backup_dir": self.paths.store_dir / "backups" if self.paths.store_dir else None,
             "kind": "store"},
            {"key": "shadow", "label": "影子记忆引擎",
             "db": self.paths.shadow_db,
             "dir": self.paths.shadow_dir,
             "backup_dir": self.paths.shadow_dir / "backups" if self.paths.shadow_dir else None,
             "kind": "engine"},
            {"key": "journal", "label": "输入 journal",
             "db": self.paths.journal_db,
             "dir": self.paths.shadow_dir,
             "backup_dir": self.paths.shadow_dir / "backups" if self.paths.shadow_dir else None,
             "kind": "journal"},
        ]
        for spec in specs:
            spec["db"] = Path(spec["db"]) if spec["db"] else None
            spec["backup_dir"] = Path(spec["backup_dir"]) if spec["backup_dir"] else None
        return specs

    def dataset(self, key: str) -> dict:
        for spec in self.dataset_specs():
            if spec["key"] == key:
                return spec
        raise ApiError("未知数据集: %s" % key, 404)

    def _require_db(self, key: str) -> Path:
        spec = self.dataset(key)
        if spec["db"] is None:
            raise ApiError("没有配置%s的数据目录" % spec["label"], 404)
        if not spec["db"].is_file():
            raise ApiError("%s数据库不存在: %s" % (spec["label"], spec["db"]), 404)
        return spec["db"]

    def dataset_summaries(self) -> list[dict]:
        items = []
        for spec in self.dataset_specs():
            db = spec["db"]
            info = _file_info(db)
            wal = _file_info(Path(str(db) + "-wal")) if db else {"bytes": 0}
            shm = _file_info(Path(str(db) + "-shm")) if db else {"bytes": 0}
            backups = self.list_backups(spec["key"])["items"] if spec["key"] != "journal" else []
            items.append({
                "key": spec["key"], "label": spec["label"], "kind": spec["kind"],
                "dir": str(spec["dir"]) if spec["dir"] else None,
                "path": info["path"], "exists": info["exists"],
                "bytes": info["bytes"], "wal_bytes": wal.get("bytes", 0),
                "shm_bytes": shm.get("bytes", 0), "mtime": info["mtime"],
                "backups": len(backups),
                "last_backup": backups[0]["at"] if backups else None,
            })
        return items

    # -- 总览 -----------------------------------------------------------

    def overview(self) -> dict:
        store = self._store_summary()
        shadow = self._shadow_summary()
        journal = self._journal_summary()
        observations = self.observations_summary()
        latency = self.latency()
        warnings = self._warnings(store, shadow, journal, latency)
        return {
            "app": {"name": APP_NAME, "version": __version__,
                    "read_only": self.read_only, "started_at": self.started_at},
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "datasets": self.dataset_summaries(),
            "counts": {"store": store, "shadow": shadow, "journal": journal,
                       "observations": observations},
            "capacity": self._capacity(store),
            "latency": latency,
            "activity": self._activity(),
            "warnings": warnings,
            "recent_observations": observations.get("recent", []),
            "engine": shadow.get("engine", {}),
        }

    def _store_summary(self) -> dict:
        summary = {"available": False, "captures": 0, "pending": 0, "events": 0,
                   "scopes": 0, "oldest": None, "newest": None, "daily_7d": 0,
                   "states": {}}
        db = self.paths.store_db
        if db is None or not db.is_file():
            return summary
        with open_ro(db) as con:
            if not table_exists(con, "dl_capture"):
                return summary
            summary["available"] = True
            summary["captures"] = scalar(con, "SELECT COUNT(*) FROM dl_capture") or 0
            summary["pending"] = scalar(
                con, "SELECT COUNT(*) FROM dl_capture WHERE state='pending'") or 0
            summary["scopes"] = scalar(
                con, "SELECT COUNT(DISTINCT scope) FROM dl_capture") or 0
            summary["oldest"] = scalar(con, "SELECT MIN(recorded_at) FROM dl_capture")
            summary["newest"] = scalar(con, "SELECT MAX(recorded_at) FROM dl_capture")
            states = con.execute(
                "SELECT state,COUNT(*) FROM dl_capture GROUP BY state").fetchall()
            summary["states"] = {row[0]: row[1] for row in states}
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            summary["daily_7d"] = round(
                (scalar(con, "SELECT COUNT(*) FROM dl_capture WHERE recorded_at>=?",
                        (cutoff,)) or 0) / 7.0, 1)
            if table_exists(con, "events"):
                summary["events"] = scalar(con, "SELECT COUNT(*) FROM events") or 0
        return summary

    def _shadow_summary(self) -> dict:
        summary = {"available": False, "events": 0, "provisional": 0,
                   "evidence": 0, "scopes": 0, "inbox": {}, "deferred": 0,
                   "pending_resolution": 0, "conflicts": 0, "latest": None,
                   "engine": {}}
        db = self.paths.shadow_db
        if db is None or not db.is_file():
            return summary
        with open_ro(db) as con:
            if not table_exists(con, "event_candidate"):
                return summary
            summary["available"] = True
            summary["events"] = scalar(con, "SELECT COUNT(*) FROM event_candidate") or 0
            summary["provisional"] = scalar(
                con, "SELECT COUNT(*) FROM event_candidate WHERE state='provisional'") or 0
            summary["scopes"] = scalar(
                con, "SELECT COUNT(DISTINCT scope_id) FROM event_candidate") or 0
            summary["latest"] = scalar(
                con, "SELECT MAX(updated_at) FROM event_candidate")
            if table_exists(con, "event_evidence"):
                summary["evidence"] = scalar(
                    con, "SELECT COUNT(*) FROM event_evidence WHERE valid=1") or 0
            if table_exists(con, "native_input_inbox"):
                rows = con.execute(
                    "SELECT state,COUNT(*) FROM native_input_inbox GROUP BY state").fetchall()
                summary["inbox"] = {row[0]: row[1] for row in rows}
                summary["deferred"] = summary["inbox"].get("DEFERRED", 0)
            if table_exists(con, "resolution_pending"):
                summary["pending_resolution"] = scalar(
                    con, "SELECT COUNT(*) FROM resolution_pending WHERE status='PENDING'") or 0
            if table_exists(con, "validation_issue"):
                summary["conflicts"] += scalar(
                    con, "SELECT COUNT(*) FROM validation_issue") or 0
            if table_exists(con, "meta"):
                for row in con.execute("SELECT key,value FROM meta").fetchall():
                    summary["engine"][row[0]] = row[1]
        return summary

    def _journal_summary(self) -> dict:
        summary = {"available": False, "records": 0, "undelivered": 0,
                   "streams": 0, "rejected": 0, "latest_attempt": None}
        path = self.paths.journal_db
        if path is None or not path.is_file():
            return summary
        with open_ro(path) as con:
            if not table_exists(con, "native_record"):
                return summary
            summary["available"] = True
            summary["records"] = scalar(con, "SELECT COUNT(*) FROM native_record") or 0
            summary["undelivered"] = scalar(
                con, "SELECT COUNT(*) FROM native_record WHERE delivered=0") or 0
            if table_exists(con, "native_stream"):
                summary["streams"] = scalar(con, "SELECT COUNT(*) FROM native_stream") or 0
            if table_exists(con, "import_attempt"):
                summary["rejected"] = scalar(
                    con, "SELECT COUNT(*) FROM import_attempt WHERE status='REJECTED'") or 0
                row = con.execute(
                    "SELECT status,detail FROM import_attempt ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    summary["latest_attempt"] = {"status": row[0], "detail": row[1]}
        return summary

    def _capacity(self, store: dict) -> dict:
        """按当前每条原话的库大小，估算 5 年与含备份的量级。"""
        path = self.paths.store_db
        try:
            db_bytes = path.stat().st_size if path and path.is_file() else 0
        except OSError:
            db_bytes = 0
        captures = store.get("captures") or 0
        # 样本太小时，固定 schema 的开销会主导“每条大小”，退回标准值。
        sufficient = captures >= 100
        per_message = db_bytes / captures if sufficient else 11 * 1024
        per_message = max(per_message, 1024)
        projections = {}
        for rate in (50, 100, 200, 300, 1000):
            base = rate * 1825 * per_message
            projections[str(rate)] = {
                "db_gb": round(base / 1024 ** 3, 2),
                "with_backups_gb": round(base * 11 / 1024 ** 3, 2),
            }
        return {"per_message_bytes": round(per_message),
                "sample_sufficient": sufficient,
                "projections_5y": projections}

    def _warnings(self, store: dict, shadow: dict, journal: dict,
                  latency: dict) -> list[dict]:
        warnings = []

        def add(level, text):
            warnings.append({"level": level, "text": text})

        if self.paths.store_dir is None and self.paths.shadow_dir is None:
            add("warn", "没有发现数字记忆数据目录；请用 --instance 指向 AstrBot 实例，"
                        "或用 --store-dir / --shadow-dir 直接指定插件数据目录。")
        if self.paths.store_dir and not store["available"]:
            add("warn", "基座数据库未发现或缺少 dl_capture 表。")
        if self.paths.shadow_dir and not shadow["available"]:
            add("warn", "影子数据库未发现或缺少 event_candidate 表。")
        if self.paths.shadow_dir and not journal["available"]:
            add("info", "未发现 native_input_journal.sqlite3：该实例可能尚未使用原生输入。")
        if shadow.get("deferred"):
            add("warn", "%d 条输入处于 DEFERRED，等待判别器或 scope 变化后复判。"
                % shadow["deferred"])
        if shadow.get("pending_resolution"):
            add("warn", "%d 条证据等待人工/后续解析。" % shadow["pending_resolution"])
        if journal.get("rejected"):
            add("warn", "journal 中有 %d 次被拒绝的导入（冲突或身份变化）。"
                % journal["rejected"])

        for spec in self.dataset_specs():
            db = spec["db"]
            if db is None or not db.is_file():
                continue
            info = _file_info(db)
            wal = _file_info(Path(str(db) + "-wal"))
            if info["bytes"] and wal.get("bytes", 0) > 256 * 1024 ** 2:
                add("warn", "%s的 WAL 超过 256 MB，建议执行 checkpoint。"
                    % spec["label"])
            try:
                backups = self.list_backups(spec["key"])["items"]
            except ApiError:
                backups = []
            if spec["key"] in ("store", "shadow"):
                if not backups:
                    add("warn", "%s还没有备份，建议立即创建。" % spec["label"])
                else:
                    try:
                        age = time.time() - datetime.fromisoformat(
                            backups[0]["at"]).timestamp()
                        if age > 7 * 86400:
                            add("warn", "%s最近一次备份已超过 7 天。" % spec["label"])
                    except (TypeError, ValueError):
                        pass

        lock_path = (self.paths.store_dir / ".memory.lock") if self.paths.store_dir else None
        if lock_path and lock_path.is_file():
            try:
                owner = lock_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                owner = ""
            if owner:
                add("info", "基座当前持有运行锁：%s" % owner)

        p95 = latency.get("p95")
        if p95 is not None and p95 > 1500:
            add("warn", "钩子 P95 为 %s ms，超过 1.5 s。" % p95)

        if not warnings:
            add("ok", "没有发现需要处理的问题。")
        return warnings

    def _activity(self) -> list[dict]:
        """最近 14 天的采集与观察次数。"""
        today = date.today()
        days = [(today - timedelta(days=offset)) for offset in range(13, -1, -1)]
        counts = {day.isoformat(): {"captures": 0, "observations": 0} for day in days}
        db = self.paths.store_db
        if db is not None and db.is_file():
            try:
                with open_ro(db) as con:
                    if table_exists(con, "dl_capture"):
                        start = days[0].isoformat()
                        rows = con.execute(
                            "SELECT substr(recorded_at,1,10) AS day,COUNT(*) "
                            "FROM dl_capture WHERE recorded_at>=? GROUP BY day",
                            (start,)).fetchall()
                        for day, count in rows:
                            if day in counts:
                                counts[day]["captures"] = count
            except (sqlite3.DatabaseError, ApiError):
                pass
        try:
            rows, _ = _tail_jsonl(self.paths.shadow_log or Path(""), limit=20000)
            for row in rows:
                stamp = _iso(row.get("ts"))
                if not stamp:
                    continue
                day = stamp[:10]
                if day in counts:
                    counts[day]["observations"] += 1
        except OSError:
            pass
        return [{"date": day.isoformat(), **counts[day.isoformat()]} for day in days]

    # -- 记忆库（基座） -------------------------------------------------

    def store_scopes(self) -> dict:
        path = self._require_db("store")
        with open_ro(path) as con:
            if not table_exists(con, "dl_capture"):
                raise ApiError("该数据库不是数字记忆基座库（缺少 dl_capture）", 409)
            rows = con.execute(
                "SELECT scope,COUNT(*) AS captures,"
                "SUM(CASE WHEN state='pending' THEN 1 ELSE 0 END) AS pending,"
                "MIN(recorded_at) AS first_at,MAX(recorded_at) AS last_at "
                "FROM dl_capture GROUP BY scope ORDER BY last_at DESC").fetchall()
            return {"items": [dict(row) for row in rows]}

    def store_captures(self, scope="", state="", q="", page=1, size=20) -> dict:
        path = self._require_db("store")
        with open_ro(path) as con:
            if not table_exists(con, "dl_capture"):
                raise ApiError("该数据库不是数字记忆基座库（缺少 dl_capture）", 409)
            where, params = [], []
            if scope:
                where.append("scope=?")
                params.append(scope)
            if state:
                where.append("state=?")
                params.append(state)
            if q:
                where.append("content LIKE ?")
                params.append("%" + q + "%")
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            select = ("SELECT seq,scope,substr(content,1,240) AS preview,"
                      "length(content) AS chars,state,recorded_at,linked_source,"
                      "linked_title FROM dl_capture" + clause + " ORDER BY seq DESC")
            count = "SELECT COUNT(*) FROM dl_capture" + clause
            return _sql_page(con, select, count, tuple(params), page, size)

    def store_capture(self, scope: str, seq) -> dict:
        if not scope:
            raise ApiError("缺少 scope")
        seq = _clamp(seq, 1, 2 ** 62, 0)
        if seq < 1:
            raise ApiError("seq 无效")
        path = self._require_db("store")
        with open_ro(path) as con:
            row = con.execute("SELECT * FROM dl_capture WHERE scope=? AND seq=?",
                              (scope, seq)).fetchone()
            if not row:
                raise ApiError("原话不存在", 404)
            data = dict(row)
            data["provenance"] = None
            if table_exists(con, "dl_import_provenance"):
                prov = con.execute(
                    "SELECT * FROM dl_import_provenance WHERE scope=? AND seq=?",
                    (scope, seq)).fetchone()
                if prov:
                    data["provenance"] = dict(prov)
            data["state_label"] = STORE_STATE_LABELS.get(data.get("state"), data.get("state"))
            return data

    def store_events(self, scope="", q="", page=1, size=20) -> dict:
        path = self._require_db("store")
        with open_ro(path) as con:
            if not table_exists(con, "events"):
                return {"items": [], "total": 0, "page": 1, "size": 20, "pages": 1}
            where, params = [], []
            if scope:
                where.append("e.scope=?")
                params.append(scope)
            if q:
                where.append("e.title LIKE ?")
                params.append("%" + q + "%")
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            select = ("SELECT e.scope,e.id,e.title,COUNT(c.seq) AS captures,"
                      "MAX(c.recorded_at) AS last_at FROM events e "
                      "LEFT JOIN dl_capture c ON c.scope=e.scope AND c.linked_source=e.id"
                      + clause + " GROUP BY e.scope,e.id ORDER BY last_at DESC")
            count = "SELECT COUNT(*) FROM events e" + clause
            return _sql_page(con, select, count, tuple(params), page, size)

    def store_delete(self, scope: str, seq=None, all_scope=False,
                     purge=True) -> dict:
        self._assert_writable()
        if not scope:
            raise ApiError("缺少 scope")
        path = self._require_db("store")
        with open_rw(path) as con:
            if not table_exists(con, "dl_capture"):
                raise ApiError("该数据库不是数字记忆基座库", 409)
            if all_scope:
                count = scalar(con, "SELECT COUNT(*) FROM dl_capture WHERE scope=?",
                               (scope,)) or 0
                if count == 0:
                    raise ApiError("该 scope 下没有原话", 404)
                tables = [row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'").fetchall()]
                con.execute("PRAGMA foreign_keys=OFF")
                try:
                    with con:
                        for table in tables:
                            try:
                                columns = {row[1] for row in con.execute(
                                    "PRAGMA table_info(%s)" % table)}
                            except sqlite3.DatabaseError:
                                continue
                            if "scope" in columns:
                                con.execute("DELETE FROM %s WHERE scope=?" % table, (scope,))
                finally:
                    con.execute("PRAGMA foreign_keys=ON")
                result = {"deleted": count, "scope": scope, "all": True}
            else:
                seq = _clamp(seq, 1, 2 ** 62, 0)
                if seq < 1:
                    raise ApiError("seq 无效")
                row = con.execute("SELECT seq FROM dl_capture WHERE scope=? AND seq=?",
                                  (scope, seq)).fetchone()
                if not row:
                    raise ApiError("原话不存在", 404)
                deps = (("dl_suggestions", "capture_seq"),
                        ("dl_new_title_suggestions", "capture_seq"),
                        ("dl_contrast_drafts", "capture_seq"),
                        ("dl_auto_model_jobs", "seq"), ("dl_auto_jobs", "seq"),
                        ("dl_import_provenance", "seq"),
                        ("dl_auto_localization", "seq"), ("dl_pending_dirty", "seq"))
                with con:
                    for table, column in deps:
                        try:
                            con.execute(
                                "DELETE FROM %s WHERE scope=? AND %s=?" % (table, column),
                                (scope, seq))
                        except sqlite3.DatabaseError:
                            pass
                    con.execute("DELETE FROM dl_capture WHERE scope=? AND seq=?",
                                (scope, seq))
                result = {"deleted": 1, "scope": scope, "seq": seq, "all": False}
            notes = []
            if purge:
                try:
                    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.DatabaseError as exc:
                    notes.append("checkpoint: %s" % exc)
                try:
                    con.execute("VACUUM")
                except sqlite3.DatabaseError as exc:
                    notes.append("VACUUM: %s" % exc)
            result["notes"] = notes
        self._audit("store_delete", "store",
                    {"scope": scope, "seq": None if all_scope else seq,
                     "all": all_scope, "purge": bool(purge)}, result, True)
        return result

    # -- 影子引擎 -------------------------------------------------------

    def shadow_scopes(self) -> dict:
        path = self._require_db("shadow")
        with open_ro(path) as con:
            if not table_exists(con, "event_candidate"):
                raise ApiError("该数据库不是影子引擎库（缺少 event_candidate）", 409)
            rows = con.execute(
                "SELECT scope_id,COUNT(*) AS events,MAX(updated_at) AS last_at,"
                "SUM(CASE WHEN state='provisional' THEN 1 ELSE 0 END) AS provisional "
                "FROM event_candidate GROUP BY scope_id ORDER BY last_at DESC").fetchall()
            evidence = {}
            if table_exists(con, "event_evidence"):
                for row in con.execute(
                        "SELECT scope_id,COUNT(*) FROM event_evidence WHERE valid=1 "
                        "GROUP BY scope_id").fetchall():
                    evidence[row[0]] = row[1]
            items = []
            for row in rows:
                item = dict(row)
                item["evidence"] = evidence.get(item["scope_id"], 0)
                items.append(item)
            return {"items": items}

    def shadow_events(self, scope="", q="", state="", page=1, size=20) -> dict:
        path = self._require_db("shadow")
        with open_ro(path) as con:
            if not table_exists(con, "event_candidate"):
                raise ApiError("该数据库不是影子引擎库", 409)
            where, params = [], []
            if scope:
                where.append("c.scope_id=?")
                params.append(scope)
            if state:
                where.append("c.state=?")
                params.append(state)
            if q:
                if table_exists(con, "event_evidence"):
                    where.append("(c.title LIKE ? OR EXISTS (SELECT 1 FROM event_evidence e "
                                 "WHERE e.event_id=c.event_id AND e.quote LIKE ?))")
                    params.extend(["%" + q + "%", "%" + q + "%"])
                else:
                    where.append("c.title LIKE ?")
                    params.append("%" + q + "%")
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            evidence = ("(SELECT COUNT(*) FROM event_evidence e WHERE e.event_id=c.event_id "
                        "AND e.valid=1)") if table_exists(con, "event_evidence") else "0"
            summary = ("(SELECT r.summary FROM event_revision r WHERE r.event_id=c.event_id "
                       "AND r.rev=c.current_revision)") if table_exists(con, "event_revision") else "NULL"
            select = ("SELECT c.event_id,c.scope_id,c.title,c.event_type,c.state,"
                      "c.narrator,c.subject,c.reality_status,c.current_revision,"
                      "c.created_at,c.updated_at," + evidence + " AS evidence," +
                      summary + " AS summary FROM event_candidate c" + clause +
                      " ORDER BY c.updated_at DESC,c.event_id")
            count = "SELECT COUNT(*) FROM event_candidate c" + clause
            return _sql_page(con, select, count, tuple(params), page, size)

    def shadow_event(self, event_id: str) -> dict:
        if not event_id:
            raise ApiError("缺少 event_id")
        path = self._require_db("shadow")
        with open_ro(path) as con:
            row = con.execute("SELECT * FROM event_candidate WHERE event_id=?",
                              (event_id,)).fetchone()
            if not row:
                raise ApiError("事件不存在", 404)
            data = {"event": dict(row), "revisions": [], "evidence": [],
                    "relations": [], "receipts": [], "items": []}
            if table_exists(con, "event_revision"):
                data["revisions"] = [dict(r) for r in con.execute(
                    "SELECT * FROM event_revision WHERE event_id=? ORDER BY rev",
                    (event_id,)).fetchall()]
            if table_exists(con, "event_evidence"):
                data["evidence"] = [dict(r) for r in con.execute(
                    "SELECT * FROM event_evidence WHERE event_id=? AND valid=1 "
                    "ORDER BY rev,scope_id,source_seq", (event_id,)).fetchall()]
            if table_exists(con, "event_relation"):
                data["relations"] = [dict(r) for r in con.execute(
                    "SELECT * FROM event_relation WHERE from_event=? OR to_event=? "
                    "ORDER BY id", (event_id, event_id)).fetchall()]
            if table_exists(con, "source_receipt"):
                data["receipts"] = [dict(r) for r in con.execute(
                    "SELECT * FROM source_receipt WHERE scope_id=? AND source_seq IN "
                    "(SELECT source_seq FROM event_evidence WHERE event_id=?)",
                    (row["scope_id"], event_id)).fetchall()]
            if table_exists(con, "memory_item"):
                data["items"] = [dict(r) for r in con.execute(
                    "SELECT * FROM memory_item WHERE event_id=? ORDER BY created_at",
                    (event_id,)).fetchall()]
            return data

    def shadow_inbox(self, scope="", state="", page=1, size=20) -> dict:
        path = self.paths.shadow_db
        if path is None or not path.is_file():
            return {"available": False, "items": [], "total": 0, "page": 1,
                    "size": 20, "pages": 1, "states": {}}
        with open_ro(path) as con:
            if not table_exists(con, "native_input_inbox"):
                return {"available": False, "items": [], "total": 0, "page": 1,
                        "size": 20, "pages": 1, "states": {}}
            states = {row[0]: row[1] for row in con.execute(
                "SELECT state,COUNT(*) FROM native_input_inbox GROUP BY state").fetchall()}
            where, params = [], []
            if scope:
                where.append("i.scope_id=?")
                params.append(scope)
            if state:
                where.append("i.state=?")
                params.append(state)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            attempt = ("LEFT JOIN native_input_attempt a ON a.key=i.key"
                       if table_exists(con, "native_input_attempt") else "")
            reason = "a.reason" if attempt else "NULL"
            epoch = "a.epoch" if attempt else "NULL"
            revision = "a.judge_revision" if attempt else "NULL"
            select = ("SELECT i.key,i.scope_id,i.state,i.source_seq,i.payload,"
                      + reason + " AS reason," + epoch + " AS epoch,"
                      + revision + " AS judge_revision FROM native_input_inbox i "
                      + attempt + clause + " ORDER BY i.rowid DESC")
            count = "SELECT COUNT(*) FROM native_input_inbox i " + attempt + clause
            page_data = _sql_page(con, select, count, tuple(params), page, size)
            for item in page_data["items"]:
                payload = _json(item.pop("payload", None), {}) or {}
                item["role"] = payload.get("role")
                item["text"] = payload.get("text")
                item["recorded_at"] = payload.get("recorded_at")
                item["stream_id"] = payload.get("stream_id")
                item["identity_mode"] = payload.get("identity_mode")
            page_data["states"] = states
            page_data["available"] = True
            return page_data

    def shadow_conflicts(self, page=1, size=20) -> dict:
        path = self.paths.shadow_db
        items = []
        if path is not None and path.is_file():
            with open_ro(path) as con:
                if table_exists(con, "resolution_pending"):
                    for row in con.execute(
                            "SELECT * FROM resolution_pending ORDER BY created_at DESC "
                            "LIMIT 200").fetchall():
                        item = dict(row)
                        item["kind"] = "resolution_pending"
                        item["detail"] = item.pop("reason", "")
                        items.append(item)
                if table_exists(con, "validation_issue"):
                    for row in con.execute(
                            "SELECT * FROM validation_issue ORDER BY created_at DESC "
                            "LIMIT 200").fetchall():
                        item = dict(row)
                        item["kind"] = "validation_issue"
                        item["detail"] = "%s: %s" % (item.get("code"), item.get("detail"))
                        items.append(item)
                if table_exists(con, "event_link_decision"):
                    for row in con.execute(
                            "SELECT * FROM event_link_decision WHERE decision IN "
                            "('CONFLICT','PROVENANCE_BLOCKED','CANNOT_LINK','DEFERRED') "
                            "ORDER BY created_at DESC LIMIT 200").fetchall():
                        item = dict(row)
                        item["kind"] = "link_decision"
                        item["detail"] = item.get("reason") or item.get("decision")
                        items.append(item)
        if self.paths.journal_db and self.paths.journal_db.is_file():
            try:
                with open_ro(self.paths.journal_db) as con:
                    if table_exists(con, "import_attempt"):
                        for row in con.execute(
                                "SELECT * FROM import_attempt WHERE status='REJECTED' "
                                "ORDER BY id DESC LIMIT 200").fetchall():
                            item = dict(row)
                            item["kind"] = "import_rejected"
                            item["detail"] = item.get("detail")
                            item["attempt"] = item.get("id")
                            item["created_at"] = None
                            items.append(item)
            except (sqlite3.DatabaseError, ApiError):
                pass
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return _list_page(items, page, size)

    # -- 观察与延迟 -----------------------------------------------------

    def observations(self, scope="", q="", days=0, page=1, size=20) -> dict:
        rows, truncated = _tail_jsonl(self.paths.shadow_log or Path(""), limit=20000)
        normalized = []
        cutoff = None
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 0
        if days > 0:
            cutoff = time.time() - days * 86400
        for row in rows:
            ts = row.get("ts")
            if cutoff is not None and (not isinstance(ts, (int, float)) or ts < cutoff):
                continue
            item = {
                "ts": ts,
                "time": _iso(ts),
                "scope": row.get("scope"),
                "query": row.get("query"),
                "items": row.get("items"),
                "event_ids": row.get("event_ids") or [],
                "host_memory_tokens": row.get("host_memory_tokens"),
                "rendered_tokens": row.get("rendered_tokens"),
                "output_status": row.get("output_status"),
                "output_reason": row.get("output_reason"),
                "budget_tokens": row.get("budget_tokens"),
                "output_reasons": row.get("output_reasons") or [],
            }
            if scope and item["scope"] != scope:
                continue
            if q and q.lower() not in str(item["query"] or "").lower():
                continue
            normalized.append(item)
        normalized.sort(key=lambda item: item.get("ts") or 0, reverse=True)
        data = _list_page(normalized, page, size)
        statuses = Counter(str(item.get("output_status") or "OBSERVE")
                           for item in normalized)
        data["outputs"] = {key: statuses.get(key, 0) for key in
                           ("INJECTED", "UNCHANGED", "EMPTY", "REJECTED",
                            "UNAVAILABLE", "OBSERVE")}
        data["truncated"] = truncated
        data["scanned"] = len(rows)
        return data

    def observations_summary(self) -> dict:
        rows, truncated = _tail_jsonl(self.paths.shadow_log or Path(""), limit=20000)
        items = [row for row in rows if isinstance(row, dict)]
        total = len(items)
        injected = sum(1 for row in items if (row.get("items") or 0) > 0)
        tokens = [row.get("rendered_tokens") for row in items
                  if isinstance(row.get("rendered_tokens"), (int, float))]
        item_counts = Counter(min(int(row.get("items") or 0), 4) for row in items)
        outputs = Counter(str(row.get("output_status") or "OBSERVE") for row in items)
        last = max((row.get("ts") or 0) for row in items) if items else None
        recent = []
        for row in sorted(items, key=lambda value: value.get("ts") or 0,
                          reverse=True)[:5]:
            recent.append({"ts": row.get("ts"), "time": _iso(row.get("ts")),
                           "scope": row.get("scope"), "query": row.get("query"),
                           "items": row.get("items"),
                           "output_status": row.get("output_status")})
        return {
            "available": bool(items) or bool(self.paths.shadow_log),
            "loaded": total, "truncated": truncated,
            "last_at": _iso(last), "injected": injected,
            "inject_rate": round(injected / total, 3) if total else None,
            "avg_items": round(sum(int(row.get("items") or 0) for row in items) / total, 2)
            if total else None,
            "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
            "histogram": {str(key): item_counts.get(key, 0) for key in range(5)},
            "outputs": {key: outputs.get(key, 0) for key in
                        ("INJECTED", "UNCHANGED", "EMPTY", "REJECTED",
                         "UNAVAILABLE", "OBSERVE")},
            "recent": recent,
        }

    def latency(self) -> dict:
        rows, truncated = _tail_jsonl(self.paths.latency_log or Path(""), limit=5000)
        values = []
        injected = 0
        for row in rows:
            try:
                values.append(float(row["ms"]))
                injected += 1 if row.get("injected") else 0
            except (KeyError, TypeError, ValueError):
                continue
        if not values:
            return {"available": False, "turns": 0, "injected": 0, "p50": None,
                    "p95": None, "max": None, "avg": None, "buckets": {},
                    "truncated": truncated}
        ordered = sorted(values)
        p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        buckets = {"0-10ms": 0, "10-50ms": 0, "50-200ms": 0, "200-1000ms": 0,
                   "1000ms+": 0}
        for value in values:
            if value < 10:
                buckets["0-10ms"] += 1
            elif value < 50:
                buckets["10-50ms"] += 1
            elif value < 200:
                buckets["50-200ms"] += 1
            elif value < 1000:
                buckets["200-1000ms"] += 1
            else:
                buckets["1000ms+"] += 1
        return {"available": True, "turns": len(values), "injected": injected,
                "p50": round(ordered[len(ordered) // 2], 2),
                "p95": round(ordered[p95_index], 2),
                "max": round(ordered[-1], 2),
                "avg": round(sum(values) / len(values), 2),
                "buckets": buckets, "truncated": truncated}

    # -- 维护 -----------------------------------------------------------

    def backups(self, dataset: str) -> dict:
        return self.list_backups(dataset)

    def list_backups(self, dataset: str) -> dict:
        spec = self.dataset(dataset)
        folder = spec["backup_dir"]
        items = []
        if folder and folder.is_dir():
            for path in folder.iterdir():
                if not path.is_file() or path.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                if not path.name.endswith(".sqlite3"):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                items.append({
                    "name": path.name, "bytes": stat.st_size,
                    "at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "kind": ("console" if path.name.startswith("console_")
                             else "plugin" if path.name.startswith("memory_")
                             else "other"),
                })
        items.sort(key=lambda item: item["at"], reverse=True)
        return {"dataset": dataset, "dir": str(folder) if folder else None,
                "items": items, "total": len(items)}

    def backup(self, dataset: str) -> dict:
        self._assert_writable()
        spec = self.dataset(dataset)
        source = self._require_db(dataset)
        folder = spec["backup_dir"]
        if folder is None:
            raise ApiError("没有可用的备份目录")
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        name = "console_%s_%s_%s.sqlite3" % (dataset, stamp, os.urandom(4).hex())
        target = folder / name
        with target.open("xb"):
            pass
        try:
            with open_ro(source) as src:
                out = sqlite3.connect(str(target))
                try:
                    src.backup(out)
                    check = out.execute("PRAGMA integrity_check").fetchone()[0]
                    if check != "ok":
                        raise ApiError("备份完整性检查失败: %s" % check, 500)
                finally:
                    out.close()
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            payload = {"name": name, "bytes": target.stat().st_size}
            self._audit("backup", dataset, {}, payload, True)
            return payload
        except Exception as exc:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            self._audit("backup", dataset, {}, str(exc), False)
            raise

    def _backup_path(self, dataset: str, name: str) -> Path:
        spec = self.dataset(dataset)
        folder = spec["backup_dir"]
        if folder is None:
            raise ApiError("没有可用的备份目录", 404)
        name = str(name or "")
        if (not name or Path(name).name != name
                or not name.lower().endswith(".sqlite3")):
            raise ApiError("备份文件名无效")
        path = folder / name
        if not path.is_file():
            raise ApiError("备份文件不存在: %s" % name, 404)
        return path

    def _backup_columns(self, con, table: str) -> dict:
        try:
            return {row[1]: row for row in con.execute(
                "PRAGMA table_info(%s)" % table)}
        except sqlite3.DatabaseError:
            return {}

    def _backup_schema_version(self, con, kind: str, tables: set, report: dict) -> None:
        """校验 schema 版本；旧库缺 dl_meta 只记 warning，读不出/非法值记 error。"""
        if kind == "engine":
            table, required = "meta", True
        elif kind == "store":
            table, required = "dl_meta", False
        else:
            return
        if table not in tables:
            # 旧版基座库没有 dl_meta；引擎库缺 meta 说明备份结构不完整。
            if required:
                report["errors"].append("missing_schema_version")
            else:
                report["warnings"].append("legacy_without_schema_version")
            return
        try:
            row = con.execute(
                "SELECT value FROM %s WHERE key='schema_version'" % table).fetchone()
        except sqlite3.DatabaseError as exc:
            report["errors"].append("schema_version_unreadable: %s" % exc)
            return
        if row is None:
            report["errors"].append("missing_schema_version")
            return
        try:
            version = int(row[0])
        except (TypeError, ValueError):
            report["errors"].append("invalid_schema_version")
            return
        if version < 1:
            report["errors"].append("invalid_schema_version")
            return
        report["counts"]["schema_version"] = version

    def _verify_backup_connection(self, con: sqlite3.Connection, kind: str) -> dict:
        report = {"ok": False, "checks": {}, "counts": {}, "errors": [],
                  "warnings": []}
        try:
            integrity = [row[0] for row in con.execute("PRAGMA integrity_check")]
        except sqlite3.DatabaseError as exc:
            report["errors"].append("not_a_database: %s" % exc)
            return report
        report["checks"]["sqlite_integrity"] = integrity == ["ok"]
        if integrity != ["ok"]:
            report["errors"].append("sqlite_integrity_failed")
            return report

        try:
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            report["errors"].append("schema_unreadable: %s" % exc)
            return report
        required = REQUIRED_SCHEMA.get(kind, {})
        missing = [table for table in required if table not in tables]
        report["checks"]["required_tables"] = not missing
        if missing:
            report["errors"].append("missing_tables: " + ",".join(missing))
            return report

        self._backup_schema_version(con, kind, tables, report)

        missing_columns = []
        missing_keys = []
        for table, columns in required.items():
            info = self._backup_columns(con, table)
            if not info:
                report["errors"].append("schema_unreadable: " + table)
                continue
            absent = [column for column in columns if column not in info]
            if absent:
                missing_columns.append(table + ":" + ",".join(absent))
            for column in REQUIRED_PRIMARY_KEYS.get(kind, {}).get(table, ()):
                row = info.get(column)
                if row is None or not row[5]:
                    missing_keys.append(table + "." + column)
        report["checks"]["required_columns"] = not missing_columns
        if missing_columns:
            # 缺列时后续扫描不能算“通过且零异常”，必须让备份判为不可用。
            report["errors"].append("missing_columns: " + ";".join(missing_columns))
        report["checks"]["primary_keys"] = not missing_keys
        if missing_keys:
            report["errors"].append("missing_primary_key: " + ";".join(missing_keys))
        if missing_columns:
            report["ok"] = False
            return report

        try:
            violations = sum(1 for _ in con.execute("PRAGMA foreign_key_check"))
            report["counts"]["foreign_key_violations"] = violations
            report["checks"]["foreign_keys"] = violations == 0
            if violations:
                report["errors"].append("foreign_key_violations")
        except sqlite3.DatabaseError as exc:
            report["checks"]["foreign_keys"] = False
            report["errors"].append("foreign_key_check_failed: %s" % exc)

        if kind == "store":
            report["counts"]["captures"] = scalar(
                con, "SELECT COUNT(*) FROM dl_capture") or 0
            report["counts"]["scopes"] = scalar(
                con, "SELECT COUNT(DISTINCT scope) FROM dl_capture") or 0
            report["counts"]["pending"] = scalar(
                con, "SELECT COUNT(*) FROM dl_capture WHERE state='pending'") or 0
            mismatches = 0
            scan_error = None
            try:
                for row in con.execute("SELECT content,content_sha FROM dl_capture"):
                    try:
                        digest = hashlib.sha256(
                            (row[0] or "").encode("utf-8")).hexdigest()
                    except (AttributeError, UnicodeError):
                        mismatches += 1
                        continue
                    if digest != row[1]:
                        mismatches += 1
            except sqlite3.DatabaseError as exc:
                scan_error = str(exc)
                report["errors"].append("capture_hash_scan_failed: %s" % exc)
            report["counts"]["capture_hash_mismatches"] = mismatches
            report["checks"]["capture_hashes"] = scan_error is None and mismatches == 0
            if mismatches:
                report["errors"].append("capture_hash_mismatch")
            if "events" not in tables:
                report["warnings"].append("missing_events_table")
        elif kind == "engine":
            report["counts"]["events"] = scalar(
                con, "SELECT COUNT(*) FROM event_candidate") or 0
            report["counts"]["scopes"] = scalar(
                con, "SELECT COUNT(DISTINCT scope_id) FROM event_candidate") or 0
            report["counts"]["revisions"] = scalar(
                con, "SELECT COUNT(*) FROM event_revision") or 0
            report["counts"]["evidence"] = scalar(
                con, "SELECT COUNT(*) FROM event_evidence WHERE valid=1") or 0
            if "memory_item" in tables:
                report["counts"]["memory_items"] = scalar(
                    con, "SELECT COUNT(*) FROM memory_item") or 0
            orphans = scalar(
                con, "SELECT COUNT(*) FROM event_evidence e LEFT JOIN "
                     "event_candidate c ON c.event_id=e.event_id WHERE c.event_id IS NULL") or 0
            report["counts"]["orphan_evidence"] = orphans
            report["checks"]["evidence_owners"] = orphans == 0
            if orphans:
                report["errors"].append("orphan_evidence")
            revisions = scalar(
                con, "SELECT COUNT(*) FROM event_revision r LEFT JOIN "
                     "event_candidate c ON c.event_id=r.event_id WHERE c.event_id IS NULL") or 0
            report["counts"]["orphan_revisions"] = revisions
            if revisions:
                report["errors"].append("orphan_revisions")
        else:
            report["counts"]["records"] = scalar(
                con, "SELECT COUNT(*) FROM native_record") or 0
            report["counts"]["undelivered"] = scalar(
                con, "SELECT COUNT(*) FROM native_record WHERE delivered=0") or 0
            if "native_stream" in tables:
                report["counts"]["streams"] = scalar(
                    con, "SELECT COUNT(*) FROM native_stream") or 0
            if "import_attempt" in tables:
                report["counts"]["rejected_attempts"] = scalar(
                    con, "SELECT COUNT(*) FROM import_attempt WHERE status='REJECTED'") or 0
        report["ok"] = not report["errors"]
        return report

    def _verify_backup_file(self, path: Path, kind: str) -> dict:
        try:
            with open_backup(path) as con:
                return self._verify_backup_connection(con, kind)
        except (sqlite3.DatabaseError, OSError) as exc:
            return {"ok": False, "checks": {}, "counts": {},
                    "errors": ["cannot_open_backup: %s" % exc], "warnings": []}

    def verify_backup(self, dataset: str, name: str) -> dict:
        """校验单个备份；只读操作，损坏文件返回 ok=False 而不是 500。"""
        spec = self.dataset(dataset)
        path = self._backup_path(dataset, name)
        report = self._verify_backup_file(path, spec["kind"])
        report.update({
            "dataset": dataset, "name": path.name,
            "bytes": path.stat().st_size,
            "at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                timespec="seconds"),
        })
        self._audit("verify_backup", dataset, {"name": path.name},
                    {"ok": report["ok"], "counts": report["counts"]}, report["ok"])
        return report

    def restore_backup(self, dataset: str, name: str) -> dict:
        """把备份恢复到 restores/ 下的新文件，绝不覆盖正在使用的数据库。"""
        self._assert_writable()
        spec = self.dataset(dataset)
        source = self._backup_path(dataset, name)
        report = self._verify_backup_file(source, spec["kind"])
        if not report["ok"]:
            self._audit("restore_backup", dataset, {"name": name},
                        {"ok": False, "errors": report["errors"]}, False)
            raise ApiError("备份校验未通过，未执行恢复: %s"
                           % ", ".join(report["errors"]), 409)
        if spec["dir"] is None:
            raise ApiError("没有可用的恢复目录")
        folder = spec["dir"] / "restores"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        target = folder / ("restored_%s_%s_%s.sqlite3"
                           % (dataset, stamp, os.urandom(4).hex()))
        with target.open("xb"):
            pass
        try:
            with open_backup(source) as src:
                out = sqlite3.connect(str(target))
                try:
                    src.backup(out)
                finally:
                    out.close()
            copied = self._verify_backup_file(target, spec["kind"])
            if not copied["ok"]:
                raise ApiError("恢复副本校验失败: %s"
                               % ", ".join(copied["errors"]), 500)
            if copied["counts"] != report["counts"]:
                raise ApiError("恢复副本计数与源备份不一致", 500)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            payload = {"dataset": dataset, "source": source.name,
                       "target": str(target), "bytes": target.stat().st_size,
                       "counts": copied["counts"], "checks": copied["checks"],
                       "warnings": copied["warnings"],
                       "note": "已写入 restores/ 新文件，未替换正在运行的数据库"}
            self._audit("restore_backup", dataset,
                        {"name": source.name, "target": target.name},
                        {"bytes": payload["bytes"], "counts": copied["counts"]}, True)
            return payload
        except Exception as exc:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            self._audit("restore_backup", dataset, {"name": name},
                        {"error": str(exc)}, False)
            raise

    def rotate(self, dataset: str, keep=7) -> dict:
        self._assert_writable()
        spec = self.dataset(dataset)
        keep = _clamp(keep, 1, 100, 7)
        folder = spec["backup_dir"]
        if folder is None or not folder.is_dir():
            raise ApiError("备份目录不存在")
        candidates = [path for path in folder.iterdir()
                      if path.is_file() and path.name.endswith(".sqlite3")
                      and (path.name.startswith("memory_") or path.name.startswith("console_"))]
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        removed = []
        cleaned = []
        for path in candidates[keep:]:
            try:
                path.unlink()
                removed.append(path.name)
                for suffix in ("-wal", "-shm", "-journal"):
                    side = Path(str(path) + suffix)
                    if side.exists():
                        side.unlink()
                        cleaned.append(side.name)
            except OSError:
                continue
        for path in candidates[:keep]:
            for suffix in ("-wal", "-shm", "-journal"):
                side = Path(str(path) + suffix)
                if side.exists():
                    try:
                        side.unlink()
                        cleaned.append(side.name)
                    except OSError:
                        pass
        payload = {"kept": keep, "removed": removed, "cleaned": cleaned,
                   "remaining": len([path for path in folder.iterdir()
                                     if path.name.endswith(".sqlite3")])}
        self._audit("rotate", dataset, {"keep": keep}, payload, True)
        return payload

    def checkpoint(self, dataset: str) -> dict:
        self._assert_writable()
        path = self._require_db(dataset)
        with open_rw(path) as con:
            rows = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            result = [list(row) for row in rows]
        payload = {"result": result, "busy": bool(result and result[0][0])}
        self._audit("checkpoint", dataset, {}, payload, True)
        return payload

    def integrity(self, dataset: str) -> dict:
        path = self._require_db(dataset)
        with open_ro(path) as con:
            rows = con.execute("PRAGMA integrity_check").fetchall()
        result = [row[0] for row in rows]
        ok = result == ["ok"]
        payload = {"ok": ok, "result": result}
        self._audit("integrity", dataset, {}, payload, True)
        return payload

    def vacuum(self, dataset: str) -> dict:
        self._assert_writable()
        path = self._require_db(dataset)
        before = path.stat().st_size if path.exists() else 0
        with open_rw(path) as con:
            con.execute("VACUUM")
        after = path.stat().st_size
        payload = {"before_bytes": before, "after_bytes": after,
                   "saved_bytes": max(0, before - after)}
        self._audit("vacuum", dataset, {}, payload, True)
        return payload

    def archive(self, months=12, export=False, kind="captures") -> dict:
        if export:
            self._assert_writable()
        months = _clamp(months, 1, 120, 12)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30 * months)).isoformat()
        if kind == "events":
            return self._archive_events(cutoff, months, export)
        path = self._require_db("store")
        with open_ro(path) as con:
            if not table_exists(con, "dl_capture"):
                raise ApiError("该数据库不是数字记忆基座库", 409)
            rows = [dict(row) for row in con.execute(
                "SELECT seq,scope,content,content_sha,recorded_at,state,linked_source,"
                "linked_title FROM dl_capture WHERE recorded_at<? ORDER BY seq",
                (cutoff,)).fetchall()]
        result = {"kind": "captures", "cutoff": cutoff, "count": len(rows),
                  "exported": bool(export)}
        if export:
            folder = self.paths.store_dir / "archives"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("captures-%s.jsonl"
                               % datetime.now().strftime("%Y%m%d-%H%M%S"))
            with target.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            result.update({"path": str(target), "bytes": target.stat().st_size})
        self._audit("archive", "store", {"months": months, "export": bool(export)},
                    {key: result[key] for key in ("count", "exported")}, True)
        return result

    def _archive_events(self, cutoff: str, months: int, export: bool) -> dict:
        path = self._require_db("shadow")
        with open_ro(path) as con:
            if not table_exists(con, "event_candidate"):
                raise ApiError("该数据库不是影子引擎库", 409)
            rows = [dict(row) for row in con.execute(
                "SELECT * FROM event_candidate WHERE created_at<? ORDER BY created_at",
                (cutoff,)).fetchall()]
            evidence = {}
            if table_exists(con, "event_evidence"):
                for row in con.execute(
                        "SELECT * FROM event_evidence WHERE valid=1").fetchall():
                    evidence.setdefault(row["event_id"], []).append(dict(row))
        for row in rows:
            row["evidence"] = evidence.get(row["event_id"], [])
        result = {"kind": "events", "cutoff": cutoff, "count": len(rows),
                  "exported": bool(export)}
        if export:
            folder = self.paths.shadow_dir / "archives"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("events-%s.jsonl"
                               % datetime.now().strftime("%Y%m%d-%H%M%S"))
            with target.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            result.update({"path": str(target), "bytes": target.stat().st_size})
        self._audit("archive", "shadow", {"months": months, "export": bool(export)},
                    {key: result[key] for key in ("count", "exported")}, True)
        return result

    # -- 环境与审计 -----------------------------------------------------

    def environment(self) -> dict:
        files = []
        for label, path, kind in describe(self.paths):
            info = _file_info(path) if kind == "file" else {
                "path": str(path) if path else None,
                "exists": bool(path and Path(path).is_dir()),
                "bytes": None, "mtime": None,
            }
            files.append({"label": label, "kind": kind, **info})
        tables = {}
        for spec in self.dataset_specs():
            if spec["db"] is None or not spec["db"].is_file():
                continue
            tables[spec["key"]] = self._table_counts(spec["db"])
        config = self._plugin_config()
        return {
            "app": {"name": APP_NAME, "version": __version__,
                    "read_only": self.read_only, "started_at": self.started_at,
                    "python": "%d.%d.%d" % tuple(sys.version_info[:3]),
                    "platform": sys.platform},
            "paths": files,
            "tables": tables,
            "config": config,
            "output": self._output_status(),
            "dataset_specs": [
                {"key": spec["key"], "label": spec["label"],
                 "db": str(spec["db"]) if spec["db"] else None,
                 "backup_dir": str(spec["backup_dir"]) if spec["backup_dir"] else None}
                for spec in self.dataset_specs()
            ],
        }

    def _table_counts(self, path: Path) -> list[dict]:
        items = []
        try:
            with open_ro(path) as con:
                names = [row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
                for name in names:
                    try:
                        count = con.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
                    except sqlite3.DatabaseError:
                        count = None
                    items.append({"name": name, "rows": count})
        except (sqlite3.DatabaseError, ApiError):
            return []
        return items

    def _output_status(self) -> dict:
        try:
            import memory_output
            from memory_output.contracts import SCHEMA_VERSION
            from memory_engine.output import MemoryOutputService  # noqa: F401
        except Exception as exc:
            return {"available": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        return {"available": True, "schema_version": SCHEMA_VERSION,
                "path": str(Path(memory_output.__file__).resolve().parent),
                "host_wiring": "SHADOW_OUTPUT_MODE=inject",
                "default": "off"}

    def _plugin_config(self) -> dict:
        if self.paths.instance is None:
            return {"available": False}
        path = (self.paths.instance / "data" / "config"
                / "astrbot_plugin_digital_memory_store_config.json")
        if not path.is_file():
            return {"available": False, "path": str(path)}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"available": False, "path": str(path), "error": str(exc)}
        if not isinstance(data, dict):
            return {"available": False, "path": str(path)}
        return {"available": True, "path": str(path),
                "values": _mask_config(data), "keys": len(data)}

    def audit(self, limit=100) -> dict:
        limit = _clamp(limit, 1, AUDIT_LIMIT, 100)
        rows, _ = _tail_jsonl(self._audit_path, limit=limit)
        rows.reverse()
        return {"items": rows, "path": str(self._audit_path), "total": len(rows)}

    def _audit(self, action: str, dataset: str, params: dict, result, ok: bool) -> None:
        record = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "action": action, "dataset": dataset, "params": params,
            "ok": ok,
            "result": result if isinstance(result, (str, int, float, bool, type(None)))
            else json.loads(json.dumps(result, ensure_ascii=False, default=str)),
        }
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_lock:
                with self._audit_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _assert_writable(self) -> None:
        if self.read_only:
            raise ApiError("控制台以只读模式启动，不能执行该操作", 403)
