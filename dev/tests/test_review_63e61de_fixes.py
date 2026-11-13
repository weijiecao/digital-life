"""审查快照 63e61de 的 F01–F10 修复回归。

每个用例对应审查报告中的一条结论，并尽量复用证据包的复现路径：约束解析的
SAME/DIFFERENT 顺序、会话人格作用域、`_checkpoint` 历史、慢模型下的聊天路径、
整请求 token 预算、journal 补投、失败公平重试、发布包完整性、抽屉删除委托与
备份结构校验。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_memory_input import Origin
from memory_engine.host_budget import HostBudget
from memory_engine.native_host import NativeInputPump, NativeInputService, journal_path_for
from memory_engine.native_judge import NativeRelationJudge
from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store

DEV = Path(__file__).resolve().parents[1]
REAL = NarrativeProvenance("user", "user", "real")


def _user(text, **extra):
    payload = {"role": "user", "content": text}
    payload.update(extra)
    return payload


class ResolverCannotLinkTests(unittest.TestCase):
    """F01：排斥关系必须真正落盘，且与 proposal 顺序无关。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = V15Store(Path(self.temp.name) / "v15.sqlite3")
        self.addCleanup(self.store.close)
        self.a = self.store.create_event_from_source(
            "s", SourceEnvelope("s", 1, "sha1", "", "青岛旅行"),
            provenance=REAL.as_dict())
        self.b = self.store.create_event_from_source(
            "s", SourceEnvelope("s", 2, "sha2", "", "北京出差"),
            provenance=REAL.as_dict())
        self.store.source_ref_upsert("s", 3, "sha3", "")
        self.source = SourceEnvelope("s", 3, "sha3", "", "补充青岛旅行")

    def _resolve(self, proposals, seq=3, source=None):
        judgment = JudgmentResult(seq, proposals, suggested_action="ATTACH_EXISTING",
                                  target_event_id=(proposals[0].candidate_event_id
                                                   if proposals else None),
                                  provenance=REAL)
        return ConstrainedResolver(self.store).resolve("s", source or self.source,
                                                       judgment)

    def test_same_then_different_persists_cannot_link(self):
        first = self._resolve([RelationProposal(self.a, "SAME_EVENT"),
                               RelationProposal(self.b, "DIFFERENT_EVENT")])
        self.assertEqual((first.attached, first.cannot_link), (1, 1))
        self.assertTrue(self.store.cannot_link_exists("s", self.a, self.b))
        # 重评时 SAME B 必须撞上刚落盘的 A/B 排斥。
        second = self._resolve([RelationProposal(self.b, "SAME_EVENT")])
        self.assertEqual((second.attached, second.conflicts), (0, 1))
        self.assertEqual(self.store.events_for_source("s", 3), [self.a])

    def test_different_before_same_is_order_independent(self):
        result = self._resolve([RelationProposal(self.b, "DIFFERENT_EVENT"),
                                RelationProposal(self.a, "SAME_EVENT")])
        self.assertEqual((result.attached, result.cannot_link), (1, 1))
        self.assertTrue(self.store.cannot_link_exists("s", self.a, self.b))

    def test_different_without_membership_reports_no_persisted_link(self):
        self.store.source_ref_upsert("s", 4, "sha4", "")
        source = SourceEnvelope("s", 4, "sha4", "", "孤立原话")
        result = self._resolve([RelationProposal(self.a, "DIFFERENT_EVENT")],
                               seq=4, source=source)
        self.assertEqual(result.cannot_link, 0)
        self.assertFalse(self.store.cannot_link_exists("s", self.a, self.b))
        decisions = self.store.db.execute(
            "SELECT decision FROM event_link_decision WHERE source_seq=? AND event_id=?",
            (4, self.a)).fetchall()
        self.assertEqual([row[0] for row in decisions], ["CANNOT_LINK"])

    def test_different_from_attached_event_is_conflict(self):
        self.store.source_ref_upsert("s", 5, "sha5", "")
        source = SourceEnvelope("s", 5, "sha5", "", "青岛又一天")
        self.store.attach_source("s", source, self.a, "seed")
        result = self._resolve([RelationProposal(self.a, "DIFFERENT_EVENT")],
                               seq=5, source=source)
        self.assertEqual((result.cannot_link, result.conflicts), (0, 1))
        self.assertEqual(self.store.events_for_source("s", 5), [self.a])


class CheckpointHistoryTests(unittest.TestCase):
    """F03：AstrBot `_checkpoint` 内部段不能拒收整份历史。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = V15Store(self.root / "engine.db")
        self.addCleanup(self.store.close)
        self.origin = Origin("webchat", "bot", "42", "p1", "c1")
        self.service = NativeInputService(self.store, journal_path_for(self.root),
                                          judge=None)
        self.addCleanup(self.service.close)

    def test_checkpoint_snapshot_is_accepted_and_queues_user_text(self):
        result = self.service.submit(
            [_user("我喜欢苹果"), {"role": "_checkpoint", "content": {"id": "cp-1"}}],
            self.origin)
        self.assertEqual(result["status"], "staged")
        self.assertEqual(result["new"], 1)
        self.assertEqual(self.service.counts(), {"QUEUED": 1})
        detail = json.loads(self.service.journal.db.execute(
            "SELECT detail FROM import_attempt WHERE status='STAGED' "
            "ORDER BY id DESC LIMIT 1").fetchone()[0])
        # 游标元数据保留，但不作为用户事实入库。
        self.assertEqual(detail["internal_segments"], 1)
        self.assertEqual(detail["checkpoint_ids"], ["cp-1"])

    def test_checkpoint_insertion_keeps_prefix_identity(self):
        history = [_user("周六去青岛。"), {"role": "assistant", "content": "记得看天气。"}]
        self.assertEqual(self.service.submit(history, self.origin)["status"], "staged")
        appended = history + [{"role": "_checkpoint", "content": {"id": "cp-2"}}]
        again = self.service.submit(appended, self.origin)
        self.assertEqual((again["status"], again["new"], again["duplicates"]),
                         ("staged", 0, 2))
        interleaved = [history[0], {"role": "_checkpoint", "content": {"id": "cp-3"}},
                       history[1]]
        self.assertEqual(self.service.submit(interleaved, self.origin)["status"], "staged")
        rows = self.service.store.db.execute(
            "SELECT COUNT(*) FROM native_input_inbox").fetchone()[0]
        self.assertEqual(rows, 2)


class RetryFairnessTests(unittest.TestCase):
    """F07：持续失败的老记录不能饿死后续输入。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = V15Store(self.root / "engine.db")
        self.addCleanup(self.store.close)
        self.origin = Origin("webchat", "bot", "42", "p1", "c1")
        self.seen = []

    def _judge(self, source, candidates, record, context):
        self.seen.append(source.source_seq)
        if source.source_seq <= 10:
            raise RuntimeError("provider down")
        return JudgmentResult(source.source_seq, suggested_action="NEW_EVENT",
                              provenance=REAL)

    def test_failed_records_back_off_and_later_input_progresses(self):
        service = NativeInputService(self.store, journal_path_for(self.root),
                                     judge=self._judge)
        self.addCleanup(service.close)
        service.submit([_user("记录%d" % index) for index in range(11)], self.origin)

        first = service.run_once(limit=10)
        self.assertEqual(first["errors"], 10)
        self.assertNotIn(11, self.seen)

        second = service.run_once(limit=10)
        self.assertIn(11, self.seen)
        self.assertEqual(second["created"], 1)

        attempts = self.store.db.execute(
            "SELECT attempts,reason,next_attempt_at FROM native_input_attempt "
            "WHERE attempts>0").fetchall()
        self.assertEqual(len(attempts), 10)
        for count, reason, next_at in attempts:
            self.assertEqual(count, 1)
            self.assertIn("RuntimeError", reason)
            self.assertGreater(next_at, 0)

    def test_backoff_expiry_and_retry_quota_are_visible(self):
        service = NativeInputService(self.store, journal_path_for(self.root),
                                     judge=self._judge)
        self.addCleanup(service.close)
        service.submit([_user("记录%d" % index) for index in range(3)], self.origin)
        service.run_once(limit=3)
        backlog = service.health()["backlog"]
        self.assertEqual(backlog["retrying"], 3)
        self.assertEqual(backlog["waiting_backoff"], 3)
        # 退避到期后（强制整批重试）失败记录仍会被再次尝试。
        with self.store._write():
            self.store.db.execute("UPDATE native_input_attempt SET next_attempt_at=0")
        before = len(self.seen)
        service.run_once(limit=3, retry_deferred=True)
        self.assertEqual(len(self.seen), before + 3)


class JournalDrainTests(unittest.TestCase):
    """F06：超过单次投递上限后，后台继续排空 journal。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = Origin("webchat", "bot", "42", "p1", "c1")

    def _service(self):
        self.store = V15Store(self.root / "engine.db")
        self.addCleanup(self.store.close)
        service = NativeInputService(self.store, journal_path_for(self.root), judge=None)
        self.addCleanup(service.close)
        return service

    @staticmethod
    def _pending(service):
        return service.journal.db.execute(
            "SELECT COUNT(*) FROM native_record WHERE delivered=0").fetchone()[0]

    def test_run_once_drains_the_remaining_backlog(self):
        service = self._service()
        initial = service.submit(
            [{"role": "assistant", "content": str(index)} for index in range(205)],
            self.origin)
        self.assertEqual(initial["delivered"]["remaining"], 5)
        self.assertEqual(service.run_once(), {"skipped": "no_judge"})
        self.assertEqual(self._pending(service), 0)
        self.assertEqual(service.counts(), {"ARCHIVED": 205})

    def test_pump_tick_drains_before_judging(self):
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown, wait=True)
        origin = self.origin
        root = self.root

        def build():
            store = V15Store(root / "engine.db")
            service = NativeInputService(store, journal_path_for(root), judge=None)
            service.submit([{"role": "assistant", "content": str(index)}
                            for index in range(205)], origin)
            return store, service

        store, service = pool.submit(build).result()
        self.addCleanup(lambda: pool.submit(service.close).result())
        self.addCleanup(lambda: pool.submit(store.close).result())
        pump = NativeInputPump(service, executor=pool, interval=0.1, limit=5)
        result = asyncio.run(pump.tick())
        self.assertEqual(result, {"skipped": "no_judge"})
        self.assertEqual(pool.submit(self._pending, service).result(), 0)


def _load_host_shadow():
    from tests.test_host_shadow_native import load_host_shadow
    return load_host_shadow()


MODULE = None


def setUpModule():
    global MODULE
    MODULE = _load_host_shadow()


class HostShadowCase(unittest.IsolatedAsyncioTestCase):
    """宿主替身：真实插件 lifecycle，不启动 AstrBot。"""

    env = {}

    async def asyncSetUp(self):
        from tests.test_native_input_host import FakeConversation
        from tests.test_host_shadow_native import FakeHostContext

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        (self.root / "data").mkdir(parents=True)
        values = {"ASTRBOT_ROOT": str(self.root), "SHADOW_PERSONA_ID": ""}
        values.update(self.env)
        patcher = patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = FakeConversation(
            "c1", [{"role": "user", "content": "周六去青岛海边散步。"}],
            persona_id="alice")
        self.context = FakeHostContext(self.conversation)
        judge = patch.object(
            MODULE.MemoryShadow, "_build_judge",
            lambda plugin, store: NativeRelationJudge(store, _scripted_provider()))
        judge.start()
        self.addCleanup(judge.stop)
        self.plugin = MODULE.MemoryShadow(self.context, {})
        await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def _query(self, sql, params=()):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.plugin.pool,
            lambda: self.plugin.store.db.execute(sql, params).fetchall())


def _scripted_provider():
    from tests.test_native_input_host import ScriptedProvider
    return ScriptedProvider()


class HostPersonaTests(HostShadowCase):
    """F02：会话人格进入真实作用域映射。"""

    async def test_two_host_personas_do_not_share_a_scope(self):
        from tests.test_native_input_host import Event, FakeConversation

        await self.plugin.capture(Event("周六去青岛海边散步。", message_id="m1"))
        await self.plugin.pump.stop()
        self.context.conversation_manager.conversation = FakeConversation(
            "c2", [{"role": "user", "content": "我昨天在北京出差。"}], persona_id="bob")
        await self.plugin.capture(Event("我昨天在北京出差。", message_id="m2"))
        await self.plugin.pump.stop()
        rows = await self._query("SELECT DISTINCT scope_id FROM native_input_inbox")
        self.assertEqual(len(rows), 2)

    async def test_capture_and_observe_use_the_same_persona_scope(self):
        from tests.test_native_input_host import Event

        await self.plugin.capture(Event("周六去青岛海边散步。", message_id="m3"))
        await self.plugin.pump.stop()
        request = SimpleNamespace(prompt="周六去青岛海边散步。", system_prompt="人格",
                                  contexts=[], extra_user_content_parts=[])
        await self.plugin.observe(Event("周六去青岛海边散步。", message_id="m4"), request)
        record = json.loads(Path(self.plugin.log).read_text(encoding="utf-8").splitlines()[-1])
        inbox = await self._query("SELECT DISTINCT scope_id FROM native_input_inbox")
        self.assertEqual(record["scope"], inbox[0][0])
        self.assertNotEqual(record["scope"], "")
        self.assertTrue(record["scope"].startswith("native-"))


class PinnedPersonaTests(HostShadowCase):
    """F02：显式固定人格与宿主不一致时拒绝采集。"""

    env = {"SHADOW_PERSONA_ID": "fixed"}

    async def test_persona_mismatch_skips_capture(self):
        from tests.test_native_input_host import Event

        await self.plugin.capture(Event("周六去青岛海边散步。", message_id="m5"))
        self.assertEqual(self.plugin.input_modes["persona_mismatch"], 1)
        rows = await self._query("SELECT COUNT(*) FROM native_input_inbox")
        self.assertEqual(rows[0][0], 0)


class HostLatencyTests(HostShadowCase):
    """F04：慢判别不再阻塞聊天路径的数据库线程。"""

    async def test_observe_does_not_wait_for_a_blocked_judge(self):
        from tests.test_native_input_host import Event

        await self.plugin.capture(Event("周六去青岛海边散步。", message_id="m6"))
        await self.plugin.pump.stop()
        started, release = threading.Event(), threading.Event()
        actual = self.plugin.service.worker.judge

        def slow_judge(*args):
            started.set()
            release.wait(10)
            return actual(*args)

        self.plugin.service.worker.judge = slow_judge
        tick = asyncio.create_task(self.plugin.pump.tick())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            request = SimpleNamespace(prompt="青岛海边散步", system_prompt="人格",
                                      contexts=[], extra_user_content_parts=[])
            await asyncio.wait_for(
                self.plugin.observe(Event("青岛海边散步", message_id="m7"), request),
                timeout=5)
        finally:
            release.set()
            await tick


class HostBudgetTests(HostShadowCase):
    """F05：宿主预算必须扣除当前请求内容。"""

    env = {"SHADOW_OUTPUT_MODE": "inject", "SHADOW_MEMORY_BUDGET": "512",
           "SHADOW_HOST_TOTAL_TOKENS": "1200", "SHADOW_OUTPUT_TOKENS": "800"}

    async def test_oversized_request_leaves_no_memory_budget(self):
        from tests.test_native_input_host import Event

        await self.plugin.capture(Event("周六去青岛海边散步。", message_id="m8"))
        await self.plugin.pump.tick()
        request = SimpleNamespace(
            prompt="周六去青岛海边散步。", system_prompt="人格", contexts=[],
            extra_user_content_parts=[SimpleNamespace(type="text",
                                                      text="长文本资料。" * 5000)])
        await self.plugin.observe(Event("周六去青岛海边散步。", message_id="m9"), request)
        record = json.loads(Path(self.plugin.log).read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["output_status"], "EMPTY")
        self.assertEqual(record["host_memory_tokens"], 0)
        self.assertEqual(len(request.extra_user_content_parts), 1)

    def test_request_tokens_are_subtracted_from_the_envelope(self):
        self.assertEqual(HostBudget(1200, 100, 200, 800, 50).memory_tokens, 50)
        self.assertEqual(HostBudget(1200, 100, 200, 800, 150).memory_tokens, 0)

    def test_request_parts_and_tools_are_counted(self):
        request = SimpleNamespace(
            prompt="问题", system_prompt="人格", contexts=[],
            extra_user_content_parts=[SimpleNamespace(type="text", text="附加" * 400)],
            image_urls=["https://example.invalid/a.png"],
            func_tool=SimpleNamespace(
                empty=lambda: False,
                tools=[SimpleNamespace(name="tool", description="说明" * 50,
                                       parameters={"type": "object"})]))
        budget = self.plugin.host_budget_for(request)
        self.assertGreater(budget.request_tokens, 400)
        self.assertEqual(budget.memory_tokens, 0)


class BundleTests(unittest.TestCase):
    """F08：当前源码构建的发布包必须能隔离导入并带控制台资源。"""

    def test_bundle_contains_new_packages_and_console_assets(self):
        from scripts.build_r0_bundle import build

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bundle.zip"
            manifest = build(archive)
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
            for required in ("astrbot_memory_input/native.py",
                             "memory_output/__init__.py",
                             "dlm_console/web/app.js",
                             "dlm_console/web/style.css",
                             "dlm_console/web/index.html",
                             "host_shadow/views/console/app.js",
                             "host_shadow/views/console/index.html",
                             "memory_engine/native_host.py",
                             "BUNDLE_MANIFEST.json"):
                self.assertIn(required, names)
            self.assertEqual(manifest["files"], len(names))
            self.assertFalse(any("__pycache__" in name for name in names))


class ConsoleDrawerBindingTests(unittest.TestCase):
    """F09：详情抽屉的删除按钮必须接入共用事件委托。"""

    def setUp(self):
        self.source = (DEV / "dlm_console" / "web" / "app.js").read_text(encoding="utf-8")

    def test_drawer_body_shares_the_action_handler(self):
        self.assertRegex(self.source, r"function handleActionClick\(")
        self.assertRegex(self.source,
                         r"for \(const root of \[view, drawerBody\]\) \{\s*"
                         r"root\.addEventListener\('click', handleActionClick\);")

    def test_drawer_renders_delete_action(self):
        self.assertIn('data-act="delete-capture"', self.source)
        self.assertIn("drawerBody.innerHTML", self.source)

    def test_embedded_console_copy_is_identical(self):
        embedded = (DEV / "host_shadow" / "views" / "console" / "app.js").read_text(
            encoding="utf-8")
        self.assertEqual(self.source, embedded)


class BackupSchemaTests(unittest.TestCase):
    """F10：结构不完整的备份不能被判为有效。"""

    STORE_SCHEMA = """
        CREATE TABLE dl_capture(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
          event_hash TEXT NOT NULL, content TEXT NOT NULL, content_sha TEXT NOT NULL,
          recorded_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
          linked_source TEXT, linked_title TEXT, UNIQUE(scope,event_hash));
    """
    ENGINE_SCHEMA = """
        CREATE TABLE event_candidate(event_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL,
          event_type TEXT NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL,
          current_revision INTEGER NOT NULL);
        CREATE TABLE event_revision(event_id TEXT NOT NULL, rev INTEGER NOT NULL,
          summary TEXT NOT NULL, status TEXT NOT NULL, modality TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE event_evidence(event_id TEXT NOT NULL, rev INTEGER NOT NULL,
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, quote TEXT NOT NULL,
          valid INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE source_ref(scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          source_hash TEXT NOT NULL, recorded_at TEXT NOT NULL, deleted_at TEXT,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE source_receipt(scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          outcome TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE scope_epoch(scope_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
        CREATE TABLE resolution_pending(pending_id TEXT PRIMARY KEY,
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          proposals_json TEXT NOT NULL, mapping_json TEXT NOT NULL,
          reason TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL);
        CREATE TABLE event_relation(id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope_id TEXT NOT NULL, from_event TEXT NOT NULL, to_event TEXT NOT NULL,
          relation_type TEXT NOT NULL, status TEXT NOT NULL, source_seq INTEGER,
          created_at TEXT NOT NULL);
        CREATE TABLE event_cannot_link(id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope_id TEXT NOT NULL, event_a TEXT NOT NULL, event_b TEXT NOT NULL,
          source_seq INTEGER, created_at TEXT NOT NULL);
    """
    JOURNAL_SCHEMA = """
        CREATE TABLE native_stream(stream_id TEXT PRIMARY KEY, mode TEXT NOT NULL);
        CREATE TABLE native_record(key TEXT PRIMARY KEY, stream_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL,
          delivered INTEGER NOT NULL DEFAULT 0);
    """

    def setUp(self):
        from dlm_console.config import Paths
        from dlm_console.core import Console

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.console = Console(Paths(workspace=root, instance=None, plugin_data=None,
                                     store_dir=None, shadow_dir=None,
                                     token="", read_only=False))

    def _verify(self, setup, kind="store"):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        setup(con)
        return self.console._verify_backup_connection(con, kind)

    @staticmethod
    def _errors(report):
        return " ".join(report["errors"])

    def test_table_without_key_columns_is_invalid(self):
        def setup(con):
            con.execute("CREATE TABLE dl_capture(seq INTEGER)")
        report = self._verify(setup)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["required_columns"])
        self.assertIn("missing_columns", self._errors(report))
        # 必需扫描没有执行，就不能同时宣称“扫描通过、零异常”。
        self.assertNotIn("capture_hashes", report["checks"])

    def test_valid_store_backup_still_passes(self):
        def setup(con):
            con.executescript(self.STORE_SCHEMA)
            content = "我喜欢吃苹果"
            con.execute(
                "INSERT INTO dl_capture(scope,event_hash,content,content_sha,"
                "recorded_at,state) VALUES(?,?,?,?,?,?)",
                ("store:a", "h1", content,
                 hashlib.sha256(content.encode()).hexdigest(),
                 "2026-11-10T10:00:00+00:00", "pending"))
        report = self._verify(setup)
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(report["checks"]["capture_hashes"])
        self.assertIn("legacy_without_schema_version", report["warnings"])

    def test_missing_primary_key_is_rejected(self):
        def setup(con):
            con.executescript(self.STORE_SCHEMA.replace(
                "seq INTEGER PRIMARY KEY AUTOINCREMENT", "seq INTEGER"))
        report = self._verify(setup)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["primary_keys"])

    def test_engine_without_schema_version_is_rejected(self):
        def setup(con):
            con.executescript(self.ENGINE_SCHEMA)
        report = self._verify(setup, "engine")
        self.assertFalse(report["ok"])
        self.assertIn("missing_schema_version", self._errors(report))

    def test_engine_with_schema_version_passes(self):
        def setup(con):
            con.executescript(self.ENGINE_SCHEMA)
            con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("INSERT INTO meta VALUES('schema_version','1')")
        report = self._verify(setup, "engine")
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["counts"]["schema_version"], 1)

    def test_journal_missing_columns_is_rejected(self):
        def setup(con):
            con.executescript("""
                CREATE TABLE native_stream(stream_id TEXT PRIMARY KEY, mode TEXT NOT NULL);
                CREATE TABLE native_record(key TEXT PRIMARY KEY, stream_id TEXT NOT NULL,
                  content_hash TEXT NOT NULL, payload TEXT NOT NULL);
            """)
        report = self._verify(setup, "journal")
        self.assertFalse(report["ok"])
        self.assertIn("missing_columns", self._errors(report))


if __name__ == "__main__":
    unittest.main()

