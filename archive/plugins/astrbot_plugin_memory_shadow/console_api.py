"""AstrBot 插件页控制台 API：复用 dlm_console.core，不另起端口。"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dlm_console.config import resolve
from dlm_console.core import ApiError, Console


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ConsoleApi:
    """把 Console 的查询/维护方法注册成宿主 Web API。

    只在 AstrBot 插件页使用；页面本身仍由 AstrBot 的会话鉴权保护。
    """

    def __init__(self, dev: Path, instance: Path | None, read_only: bool = False):
        self.dev = Path(dev)
        self.instance = Path(instance) if instance else None
        self.read_only = read_only
        self.executor = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="dlconsole")
        self.console = None
        self.error = None
        self._json = None
        self._error = None
        self._request = None

    # -- 生命周期 -------------------------------------------------------

    def console_instance(self) -> Console:
        if self.console is not None:
            return self.console
        if self.error is not None:
            raise RuntimeError(self.error)
        try:
            paths = resolve(self.dev.parent, instance=str(self.instance)
                            if self.instance else None,
                            read_only=self.read_only)
            self.console = Console(paths)
        except Exception as exc:
            self.error = type(exc).__name__ + ": " + str(exc)
            raise
        return self.console

    def close(self):
        self.executor.shutdown(wait=False)

    # -- 注册 -----------------------------------------------------------

    def register(self, register) -> int:
        try:
            from astrbot.api.web import error_response, json_response, request
        except Exception:
            return 0
        self._json = json_response
        self._error = error_response
        self._request = request
        routes = [
            ("overview", self._overview, ["GET"], "控制台总览"),
            ("environment", self._environment, ["GET"], "控制台环境"),
            ("store/scopes", self._store_scopes, ["GET"], "基座作用域"),
            ("store/captures", self._store_captures, ["GET"], "基座原话"),
            ("store/capture", self._store_capture, ["GET"], "基座原话详情"),
            ("store/events", self._store_events, ["GET"], "基座事件"),
            ("shadow/scopes", self._shadow_scopes, ["GET"], "影子作用域"),
            ("shadow/events", self._shadow_events, ["GET"], "影子事件"),
            ("shadow/event", self._shadow_event, ["GET"], "影子事件详情"),
            ("shadow/inbox", self._shadow_inbox, ["GET"], "输入队列"),
            ("shadow/conflicts", self._shadow_conflicts, ["GET"], "冲突"),
            ("shadow/observations", self._shadow_observations, ["GET"], "观察日志"),
            ("shadow/latency", self._shadow_latency, ["GET"], "钩子延迟"),
            ("maintenance/backups", self._backups, ["GET"], "备份列表"),
            ("maintenance/audit", self._audit, ["GET"], "操作审计"),
            ("maintenance/backup", self._backup, ["POST"], "创建备份"),
            ("maintenance/verify_backup", self._verify_backup, ["POST"], "校验备份"),
            ("maintenance/restore", self._restore, ["POST"], "恢复副本"),
            ("maintenance/rotate", self._rotate, ["POST"], "轮转备份"),
            ("maintenance/checkpoint", self._checkpoint, ["POST"], "WAL checkpoint"),
            ("maintenance/integrity", self._integrity, ["POST"], "完整性检查"),
            ("maintenance/vacuum", self._vacuum, ["POST"], "VACUUM"),
            ("maintenance/archive", self._archive, ["POST"], "归档导出"),
            ("store/delete", self._store_delete, ["POST"], "删除基座原话"),
        ]
        base = "/astrbot_plugin_memory_shadow/console/"
        for suffix, handler, methods, desc in routes:
            register(base + suffix, handler, methods, desc)
        return len(routes)

    # -- 基础 -----------------------------------------------------------

    async def _call(self, func):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, func)

    async def _run(self, func):
        try:
            return self._json(await self._call(func))
        except ApiError as exc:
            return self._error(str(exc), status_code=exc.status)
        except Exception as exc:
            return self._error("%s: %s" % (type(exc).__name__, exc),
                               status_code=500)

    async def _body(self) -> dict:
        try:
            value = await self._request.json(default={})
        except Exception:
            value = {}
        return value if isinstance(value, dict) else {}

    def _query(self, name, default=""):
        return self._request.query.get(name, default)

    # -- GET ------------------------------------------------------------

    async def _overview(self):
        return await self._run(lambda: self.console_instance().overview())

    async def _environment(self):
        return await self._run(lambda: self.console_instance().environment())

    async def _store_scopes(self):
        return await self._run(lambda: self.console_instance().store_scopes())

    async def _store_captures(self):
        scope = self._query("scope")
        state = self._query("state")
        text = self._query("q")
        page = _int(self._query("page", 1), 1)
        size = _int(self._query("size", 20), 20)
        return await self._run(lambda: self.console_instance().store_captures(
            scope, state, text, page, size))

    async def _store_capture(self):
        scope = self._query("scope")
        seq = _int(self._query("seq", 0), 0)
        return await self._run(lambda: self.console_instance().store_capture(
            scope, seq))

    async def _store_events(self):
        scope = self._query("scope")
        text = self._query("q")
        page = _int(self._query("page", 1), 1)
        size = _int(self._query("size", 20), 20)
        return await self._run(lambda: self.console_instance().store_events(
            scope, text, page, size))

    async def _shadow_scopes(self):
        return await self._run(lambda: self.console_instance().shadow_scopes())

    async def _shadow_events(self):
        scope = self._query("scope")
        text = self._query("q")
        state = self._query("state")
        page = _int(self._query("page", 1), 1)
        size = _int(self._query("size", 20), 20)
        return await self._run(lambda: self.console_instance().shadow_events(
            scope, text, state, page, size))

    async def _shadow_event(self):
        event_id = self._query("event_id")
        return await self._run(lambda: self.console_instance().shadow_event(
            event_id))

    async def _shadow_inbox(self):
        scope = self._query("scope")
        state = self._query("state")
        page = _int(self._query("page", 1), 1)
        size = _int(self._query("size", 20), 20)
        return await self._run(lambda: self.console_instance().shadow_inbox(
            scope, state, page, size))

    async def _shadow_conflicts(self):
        page = _int(self._query("page", 1), 1)
        size = _int(self._query("size", 20), 20)
        return await self._run(lambda: self.console_instance().shadow_conflicts(
            page, size))

    async def _shadow_observations(self):
        scope = self._query("scope")
        text = self._query("q")
        days = _int(self._query("days", 0), 0)
        page = _int(self._query("page", 1), 1)
        size = _int(self._query("size", 20), 20)
        return await self._run(lambda: self.console_instance().observations(
            scope, text, days, page, size))

    async def _shadow_latency(self):
        return await self._run(lambda: self.console_instance().latency())

    async def _backups(self):
        dataset = self._query("dataset", "store")
        return await self._run(lambda: self.console_instance().backups(dataset))

    async def _audit(self):
        limit = _int(self._query("limit", 100), 100)
        return await self._run(lambda: self.console_instance().audit(limit))

    # -- POST -----------------------------------------------------------

    async def _backup(self):
        body = await self._body()
        dataset = body.get("dataset", "store")
        return await self._run(lambda: self.console_instance().backup(dataset))

    async def _verify_backup(self):
        body = await self._body()
        dataset = body.get("dataset", "store")
        name = body.get("name", "")
        return await self._run(
            lambda: self.console_instance().verify_backup(dataset, name))

    async def _restore(self):
        body = await self._body()
        dataset = body.get("dataset", "store")
        name = body.get("name", "")
        return await self._run(
            lambda: self.console_instance().restore_backup(dataset, name))

    async def _rotate(self):
        body = await self._body()
        dataset = body.get("dataset", "store")
        keep = body.get("keep", 7)
        return await self._run(lambda: self.console_instance().rotate(dataset, keep))

    async def _checkpoint(self):
        body = await self._body()
        dataset = body.get("dataset", "store")
        return await self._run(
            lambda: self.console_instance().checkpoint(dataset))

    async def _integrity(self):
        body = await self._body()
        dataset = body.get("dataset", "store")
        return await self._run(
            lambda: self.console_instance().integrity(dataset))

    async def _vacuum(self):
        body = await self._body()
        dataset = body.get("dataset", "store")
        return await self._run(lambda: self.console_instance().vacuum(dataset))

    async def _archive(self):
        body = await self._body()
        months = body.get("months", 12)
        export = bool(body.get("export"))
        kind = body.get("kind", "captures")
        return await self._run(lambda: self.console_instance().archive(
            months, export, kind))

    async def _store_delete(self):
        body = await self._body()
        scope = body.get("scope", "")
        seq = body.get("seq")
        all_scope = bool(body.get("all"))
        backup = bool(body.get("backup", True))
        purge = bool(body.get("purge", True))

        def work():
            c = self.console_instance()
            backup_result = None
            if backup:
                backup_result = c.backup("store")
            result = c.store_delete(scope, seq, all_scope, purge)
            if backup_result:
                result["backup"] = backup_result
            return result

        return await self._run(work)
