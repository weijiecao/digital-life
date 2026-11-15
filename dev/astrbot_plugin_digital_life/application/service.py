"""统一在线业务入口。写操作共用宿主的数据库线程和维护闸门。"""

import asyncio
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .storage import atomic_json, read_json, validate_database


class ApplicationService:
    def __init__(self, plugin):
        self.plugin = plugin
        self.busy = False
        self.opening = False
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.gap_lock = threading.Lock()
        self.phase = "READY"
        self.error = None

    def _owner(self):
        p = self.plugin
        if p.lifecycle != "RUNNING" or p.console_read_only:
            raise ValueError("runtime is closed or management is read-only")
        if not p._commit_allowed():
            raise ValueError("runtime ownership is unavailable")

    async def db(self, func):
        return await asyncio.get_running_loop().run_in_executor(self.plugin.pool, func)

    def note_gap(self, origin):
        with self.gap_lock:
            path = self.plugin.base / "maintenance_gaps.json"
            state = read_json(path, {})
            key = origin.stream_id if origin else "unresolved"
            state[key] = {
                "count": state.get(key, {}).get("count", 0) + 1,
                "last_at": time.time(),
            }
            # 每条记录只有身份和计数；当前消息正文继续由宿主保留，下一次历史同步补录。
            atomic_json(path, dict(list(state.items())[-4096:]))

    async def action(self, name, body=None):
        """请求取消不会中断已开始的维护切换；状态接口可查询最终结果。"""
        task = asyncio.create_task(self._action(name, body or {}))
        self.tasks.add(task)

        def done(t):
            self.tasks.discard(t)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _action(self, name, body):
        p = self.plugin
        if name == "status":
            result = await p.status_async()
            result.update(
                management_read_only=p.console_read_only,
                maintenance={
                    "phase": self.phase,
                    "busy": self.busy,
                    "error": self.error,
                },
                backups=p.generations.backups(),
                restore=read_json(p.generations.operation),
                gaps=read_json(p.base / "maintenance_gaps.json", {}),
            )
            if p.migration is not None and not self.busy:
                result["migrations"] = await self.db(
                    lambda: p.migration.status() if p.migration else []
                )
            return result
        async with self.lock:
            self._owner()
            self.error = None
            if name in ("backup", "restore"):
                return await self._maintenance(name, body)

            def work():
                if not p._commit_allowed() or not p._ensure():
                    raise ValueError("memory service unavailable")
                if name == "migration_create":
                    return p.migration.create(
                        body["source"], body.get("mapping", {}), body.get("source_id")
                    )
                if name == "shadow_import":
                    if body.get("source_stopped") is not True:
                        raise ValueError(
                            "stop the source instance before importing the database pair"
                        )
                    return p.generations.import_shadow(body["source"])
                if name == "migration_batch":
                    return p.migration.batch(body["id"], body.get("limit", 200))
                if name == "migration_mapping":
                    return p.migration.update_mapping(body["id"], body["mapping"])
                if name == "migration_release":
                    return p.migration.release(body["id"])
                if name == "verify_backup":
                    return p.generations.validate_backup(body["id"])
                if name == "withdraw":
                    scope, seq = body["scope"], int(body["source_seq"])
                    row = p.store.db.execute(
                        "SELECT key FROM native_input_inbox WHERE scope_id=? AND source_seq=?",
                        (scope, seq),
                    ).fetchone()
                    if row is None:
                        raise ValueError("source not found in this scope")
                    # 先写重放屏障，再原子失效业务证据；任一中断均不允许再投递原文。
                    p.service.journal.tombstone(row[0], "operator_withdrawal")
                    with p.store.unit_of_work():
                        p.engine.delete_source(scope, seq)
                        p.store.db.execute(
                            "UPDATE native_input_inbox SET state='WITHDRAWN' WHERE key=?",
                            (row[0],),
                        )
                    p.followup.clear()
                    return {
                        "withdrawn": True,
                        "scope": scope,
                        "source_seq": seq,
                        "physical_delete": False,
                    }
                raise ValueError("unknown runtime action")

            try:
                return await self.db(work)
            except Exception as exc:
                self.error = type(exc).__name__ + ": " + str(exc)
                raise

    async def _open(self):
        p = self.plugin
        p.error = None
        self.opening = True
        try:
            if not await self.db(p._ensure):
                raise RuntimeError(p.error or "cannot open generation")
        finally:
            self.opening = False

    def _paths(self):
        p = self.plugin
        p.data_dir = p.generations.active
        p.store_generation = p.generations.current["epoch"]
        p.commit_state_path = p.data_dir / "commit_state.json"
        p.last_commit_at = p._load_commit_time()
        p.log = p.data_dir / "shadow_events.jsonl"
        p._inbox_snapshot = {}
        p._last_commit_marker = 0
        p.last_commit_observed_at = None
        p.output_service = None
        p.engine = None
        p.migration = None
        p.followup.clear()
        if p.console_api:
            p.console_api.data_dir = p.data_dir
            p.console_api.console = None
            p.console_api.error = None

    async def _maintenance(self, name, body):
        p = self.plugin
        op = None
        # 先验证备份，损坏/错误 ID 不影响正在运行的实例。
        if name == "restore":
            await self.db(lambda: p.generations.validate_backup(body["id"]))
        self.busy = True
        self.phase = "QUIESCE"
        try:
            if p.pump is not None:
                await p.pump.stop()
                p.pump = None
            # 取消 pump 不会停止模型线程；必须等线程退出，才能关闭它引用的数据。
            await asyncio.to_thread(p.judge_pool.shutdown, True)
            p.judge_pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="dljudge"
            )
            await self.db(lambda: None)
            if p.console_api:
                await asyncio.get_running_loop().run_in_executor(
                    p.console_api.executor, lambda: None
                )
            if name == "backup":
                await self._open()
                self.phase = "SNAPSHOT"
                return await self.db(
                    lambda: p.generations.create_backup(
                        {"mode": p.mode, "plugin_version": "0.7.0-dev.2"}
                    )
                )
            await self.db(
                lambda: p.generations.create_backup(
                    {"mode": p.mode, "reason": "before_restore"}
                )
            )
            self.phase = "STAGE"
            op = await self.db(lambda: p.generations.prepare_restore(body["id"]))
            self.phase = "CLOSE"
            await self.db(p._close)
            self.phase = "SWITCH"
            p.generations.switch(op)
            self._paths()
            self.phase = "OPEN"
            await self._open()
            self.phase = "VERIFY"
            await self.db(
                lambda: validate_database(
                    p.data_dir / "shadow.sqlite3",
                    ("native_input_inbox", "event_candidate"),
                )
            )
            # 只有重开与验证成功才把切换记录标记为完成。
            p.generations.finish(op)
            return {
                "restored": True,
                "operation": op["id"],
                "generation": p.store_generation,
            }
        except BaseException as exc:
            self.error = type(exc).__name__ + ": " + str(exc)
            if op is not None:
                await self.db(p._close)
                p.generations.rollback(op)
                self._paths()
                await self._open()
            raise
        finally:
            self.busy = False
            self.phase = "READY" if p.store is not None else "DEGRADED"
            if p.lifecycle == "RUNNING":
                p._start_pump()

    async def wait_idle(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        async with self.lock:
            pass
