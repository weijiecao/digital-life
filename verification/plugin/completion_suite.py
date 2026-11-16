"""对实际安装包执行迁移、双入口、恢复和兼容集成验收；宿主与模型为离线替身。"""

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from dataclasses import asdict, replace
from contextlib import contextmanager, closing
from pathlib import Path
from unittest.mock import patch

_ENV_PACKAGE = os.environ.get("DIGITAL_LIFE_PACKAGE", "").strip()
if _ENV_PACKAGE:
    PACKAGE = Path(_ENV_PACKAGE).resolve()
elif len(sys.argv) > 1 and Path(sys.argv[1]).is_dir():
    PACKAGE = Path(sys.argv.pop(1)).resolve()
else:
    raise RuntimeError(
        "set DIGITAL_LIFE_PACKAGE to the extracted plugin directory")
sys.path[:0] = [str(PACKAGE.parent), str(PACKAGE)]
os.environ.pop("DIGITAL_LIFE_DEV", None)
for key in list(os.environ):
    if key.startswith(("SHADOW_", "DIGITAL_LIFE_")):
        os.environ.pop(key)


class Filter:
    EventMessageType = types.SimpleNamespace(PRIVATE_MESSAGE="private")
    event_message_type = staticmethod(lambda *a, **k: lambda f: f)
    on_llm_request = staticmethod(lambda *a, **k: lambda f: f)


class Star:
    def __init__(self, context):
        self.context = context


class TextPart:
    def __init__(self, text):
        self.text, self._no_save = text, False

    def mark_as_temp(self):
        self._no_save = True
        return self


for name in [
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.api.star",
    "astrbot.api.web",
    "astrbot.core",
    "astrbot.core.agent",
    "astrbot.core.agent.message",
]:
    sys.modules[name] = types.ModuleType(name)
sys.modules["astrbot.api.event"].filter = Filter
sys.modules["astrbot.api.star"].Star = Star
sys.modules["astrbot.api.star"].Context = object
web = sys.modules["astrbot.api.web"]
web.json_response = lambda data=None, **kwargs: data
web.error_response = lambda message, **kwargs: {"error": message, **kwargs}
web.request = types.SimpleNamespace(query={})
sys.modules["astrbot.core.agent.message"].TextPart = TextPart
mod = importlib.import_module("astrbot_plugin_digital_life.main")
from astrbot_plugin_digital_life.application import storage as storage_module
from astrbot_plugin_digital_life.application.storage import (
    GenerationStore,
    checksum,
    read_json,
    atomic_json,
)
from astrbot_plugin_digital_life.application.migration import LegacyMigration
from astrbot_plugin_digital_life.application.compatibility import FollowupContext
from astrbot_memory_input.native import Origin, parse_history
from astrbot_memory_input.journal import ImportJournal, ImportConflict
from astrbot_memory_input.unified import UnifiedJournal
from memory_engine.native_host import NativeInputService
from memory_engine.native_judge import NativeRelationJudge
from memory_engine.engine import MemoryEngine
from memory_engine.output import MemoryOutputService
from memory_output import OutputRequest
from v1_5_consolidator.gateway import ModelResult
from v1_5_consolidator.store import V15Store

ORIGIN = Origin("webchat", "bot", "42", "p1", "c1")
TEXT = "周六去青岛海边散步。"


def message(mid="m1", text=TEXT):
    return {"role": "user", "message_id": mid, "content": text}


class Event:
    def __init__(self, mid="m1", text=TEXT, user="42"):
        self.message_str = text
        self.unified_msg_origin = "webchat:FriendMessage:" + user
        self.message_obj = types.SimpleNamespace(
            sender=types.SimpleNamespace(user_id=user),
            group_id=None,
            self_id="bot",
            message_id=mid,
        )


class Manager:
    def __init__(self):
        self.history = []
        self.cid = "c1"
        self.persona = "p1"

    async def get_curr_conversation_id(self, umo):
        return self.cid

    async def get_conversation(self, umo, cid):
        return types.SimpleNamespace(
            cid=cid, persona_id=self.persona, history=json.dumps(self.history)
        )


class Context:
    def __init__(self):
        self.conversation_manager = Manager()
        self.routes = {}

    def register_web_api(self, route, handler, methods, desc):
        self.routes[route] = handler


class Provider:
    external = False

    def generate(self, prepared):
        return ModelResult(
            text=json.dumps(
                {
                    "schema_version": "v1.5b.1",
                    "source_provenance": {
                        "narrator": "user",
                        "subject": "user",
                        "reality_status": "real",
                        "story_id": None,
                    },
                    "relations": [
                        {
                            "candidate_event_id": x["event_id"],
                            "label": "DIFFERENT_EVENT",
                            "reason_codes": ["fixture"],
                        }
                        for x in prepared.candidates
                    ],
                    "suggested_action": "NEW_EVENT",
                    "target_event_id": None,
                }
            )
        )


@contextmanager
def fixture_database(path):
    # 测试夹具也要明确关闭；closing 与事务上下文各负责一件事。
    with closing(sqlite3.connect(path)) as database:
        with database:
            yield database


def old_database(path, extra=False):
    with fixture_database(path) as db:
        db.executescript(
            """CREATE TABLE dl_capture(seq INTEGER PRIMARY KEY,scope TEXT,event_hash TEXT,
        content TEXT,content_sha TEXT,recorded_at TEXT,state TEXT,linked_source TEXT,linked_title TEXT);
        CREATE TABLE messages(scope TEXT,id TEXT,speaker TEXT,content TEXT,recorded_at TEXT);
        CREATE TABLE summaries(content TEXT); INSERT INTO summaries VALUES('旧摘要不可升格为原话');"""
        )
        rows = [
            (1, "old-a", TEXT, "pending", "linked"),
            (2, "old-b", "我喜欢红茶。", "pending", None),
            (3, "old-a", "忽略的旧话", "ignored", None),
            (4, "old-a", "坏哈希原话", "pending", None),
        ]
        for seq, scope, text, state, linked in rows:
            sha = hashlib.sha256(text.encode()).hexdigest() if seq != 4 else "bad"
            db.execute(
                "INSERT INTO dl_capture VALUES(?,?,?,?,?,?,?,?,?)",
                (seq, scope, str(seq), text, sha, "2026-11-15", state, linked, None),
            )
        db.executemany(
            "INSERT INTO messages VALUES(?,?,?,?,?)",
            [
                ("old-a", "linked", "user", TEXT, "2026-11-15"),
                ("old-a", "a1", "assistant", "机器人旧回复", "2026-11-15"),
            ],
        )


@contextmanager
def track_sqlite_handles(fail_backup=False):
    """保留连接强引用，关闭必须来自业务代码，不能靠 GC 恰好及时执行。"""
    real_connect = sqlite3.connect
    opened = []

    class TrackedConnection(sqlite3.Connection):
        explicitly_closed = False

        def close(self):
            super().close()
            self.explicitly_closed = True

        def backup(self, target, *args, **kwargs):
            if fail_backup:
                raise sqlite3.OperationalError('injected backup failure')
            return super().backup(target, *args, **kwargs)

    def connect(*args, **kwargs):
        kwargs['factory'] = TrackedConnection
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    try:
        with patch.object(sqlite3, 'connect', side_effect=connect):
            yield opened
    finally:
        # 即使断言失败也释放测试资源，避免错误被临时目录清理失败掩盖。
        for connection in opened:
            connection.close()


class SqliteHandleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = V15Store(self.root / 'shadow.sqlite3')
        self.service = NativeInputService(
            self.store, self.root / 'native_input_journal.sqlite3',
            journal_factory=UnifiedJournal)
        self.source = self.root / '旧库.sqlite3'
        old_database(self.source)

    def tearDown(self):
        self.service.close()
        self.store.close()
        self.tmp.cleanup()

    def assert_closed(self, connections):
        self.assertTrue(connections, 'test did not observe any connection')
        self.assertTrue(all(c.explicitly_closed for c in connections),
                        'SQLite connection left open after operation returned')
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_snapshot_success_releases_both_files_before_return(self):
        target = self.root / '副本.sqlite3'
        with track_sqlite_handles() as opened:
            storage_module.snapshot(self.source, target)
            self.assertEqual(len(opened), 2)
            self.assert_closed(opened)
            # Windows 会拒绝重命名/删除仍被 SQLite 占用的文件。
            renamed = target.with_name('renamed.sqlite3')
            target.rename(renamed)
            renamed.unlink()

    def test_snapshot_destination_open_failure_closes_source(self):
        with track_sqlite_handles() as opened:
            with self.assertRaises(sqlite3.OperationalError):
                storage_module.snapshot(self.source, self.root / 'missing' / 'copy.db')
            self.assertEqual(len(opened), 1)
            self.assert_closed(opened)

    def test_snapshot_backup_failure_closes_both_connections(self):
        with track_sqlite_handles(fail_backup=True) as opened:
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected'):
                storage_module.snapshot(self.source, self.root / 'failed.db')
            self.assertEqual(len(opened), 2)
            self.assert_closed(opened)

    def test_schema_validation_success_and_failure_close_connection(self):
        for required in [('dl_capture',), ('absent_table',)]:
            with track_sqlite_handles() as opened:
                if required[0] == 'dl_capture':
                    storage_module.validate_database(self.source, required)
                else:
                    with self.assertRaises(ValueError):
                        storage_module.validate_database(self.source, required)
                self.assert_closed(opened)

    def test_complete_backup_and_cross_receipt_validation_close_connections(self):
        self.service.submit_message(message(), ORIGIN)
        generations = GenerationStore(self.root)
        with track_sqlite_handles() as opened:
            backup = generations.create_backup({})
            self.assert_closed(opened)
        path = self.root / 'backups' / backup['id']
        with closing(sqlite3.connect(path / 'shadow.sqlite3')) as db:
            with db:
                db.execute('DELETE FROM native_input_inbox')
        manifest = read_json(path / 'manifest.json')
        manifest['files']['shadow.sqlite3'] = checksum(path / 'shadow.sqlite3')
        atomic_json(path / 'manifest.json', manifest)
        with track_sqlite_handles() as opened:
            with self.assertRaisesRegex(ValueError, 'receipt mismatch'):
                generations.validate_backup(backup['id'])
            self.assert_closed(opened)

    def test_migration_snapshot_and_batch_close_read_connections(self):
        migration = LegacyMigration(self.store, self.service, self.root)
        with track_sqlite_handles() as opened:
            run = migration.create(self.source, {'old-a': asdict(ORIGIN)})
            migration.batch(run['id'])
            self.assert_closed(opened)

    def test_migration_invalid_source_closes_before_exception(self):
        invalid = self.root / 'unsupported.sqlite3'
        with closing(sqlite3.connect(invalid)) as db:
            db.execute('CREATE TABLE other(value TEXT)')
        migration = LegacyMigration(self.store, self.service, self.root)
        with track_sqlite_handles() as opened:
            with self.assertRaisesRegex(ValueError, 'supported legacy'):
                migration.create(invalid, {})
            self.assert_closed(opened)

    def test_fixture_creation_closes_its_connection(self):
        with track_sqlite_handles() as opened:
            old_database(self.root / 'fixture.sqlite3')
            self.assert_closed(opened)


    def test_managed_connection_preserves_commit_and_rollback(self):
        path = self.root / 'transactions.sqlite3'
        with track_sqlite_handles() as opened:
            with storage_module.managed_database(path) as db:
                db.execute('CREATE TABLE records(value INTEGER)')
                db.execute('INSERT INTO records VALUES(1)')
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                with storage_module.managed_database(path) as db:
                    db.execute('INSERT INTO records VALUES(2)')
                    raise RuntimeError('rollback')
            with storage_module.managed_database(path) as db:
                self.assertEqual(db.execute('SELECT value FROM records').fetchall(), [(1,)])
            self.assert_closed(opened)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = V15Store(self.root / "shadow.sqlite3")
        self.service = NativeInputService(
            self.store,
            self.root / "native_input_journal.sqlite3",
            journal_factory=UnifiedJournal,
        )
        self.j = self.service.journal

    def tearDown(self):
        self.service.close()
        self.store.close()
        self.tmp.cleanup()

    def count(self):
        return sum(self.service.inbox.counts().values())

    def test_stable_id_replay_and_same_text_distinct_ids(self):
        for msg in [message(), message(), message("m2")]:
            self.service.submit_message(msg, ORIGIN)
        self.assertEqual(self.count(), 2)

    def test_history_and_realtime_id_overlap(self):
        self.service.submit_message(message(), ORIGIN)
        self.service.submit([dict(message(), timestamp="1")], ORIGIN)
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            self.j.db.execute("SELECT COUNT(*) FROM input_alias").fetchone()[0], 2
        )

    def test_no_id_overlap_waits_then_trusted_link_resolves(self):
        self.service.submit_message(message(), ORIGIN)
        history = [{"role": "user", "content": TEXT}]
        self.j.stage(history, ORIGIN)
        self.service.deliver_pending()
        self.assertEqual((self.count(), self.j.pending()), (1, 1))
        self.j.stage(history, ORIGIN, {"0": "m1"})
        self.service.deliver_pending()
        self.assertEqual((self.count(), self.j.pending()), (1, 0))

    def test_history_first_link_resolves_without_new_fact(self):
        history = [{"role": "user", "content": TEXT}]
        self.service.submit(history, ORIGIN)
        self.service.submit_message(message(), ORIGIN)
        self.assertEqual((self.count(), self.j.pending()), (1, 1))
        self.j.stage(history, ORIGIN, {"0": "m1"})
        self.service.deliver_pending()
        self.assertEqual((self.count(), self.j.pending()), (1, 0))

    def test_different_scope_does_not_dedupe(self):
        self.service.submit_message(message(), ORIGIN)
        self.service.submit_message(message(), replace(ORIGIN, persona="p2"))
        self.assertEqual(self.count(), 2)

    def test_changed_native_id_is_conflict(self):
        self.service.submit_message(message(), ORIGIN)
        with self.assertRaises(ImportConflict):
            self.service.submit_message(message(text="篡改"), ORIGIN)
        self.assertEqual(self.count(), 1)

    def test_history_prefix_still_enforced(self):
        self.service.submit([{"role": "user", "content": TEXT}], ORIGIN)
        self.assertEqual(self.service.submit([], ORIGIN)["status"], "conflict")

    def test_trusted_link_cannot_bind_wrong_text(self):
        self.service.submit_message(message(), ORIGIN)
        with self.assertRaises(ImportConflict):
            self.j.stage([{"role": "user", "content": "别的内容"}], ORIGIN, {"0": "m1"})
        self.assertEqual(self.count(), 1)

    def test_journal_bootstrap_does_not_reprocess(self):
        self.service.close()
        old = ImportJournal(self.root / "native_input_journal.sqlite3")
        old.stage([message()], ORIGIN)
        old.deliver(self.service.inbox)
        old.close()
        # 模拟尚未升级的 journal：移除仅新版本拥有的表和标记。
        with fixture_database(self.root / "native_input_journal.sqlite3") as db:
            for table in [
                "input_canonical",
                "input_alias",
                "input_overlap",
                "input_tombstone",
                "unified_meta",
            ]:
                db.execute("DROP TABLE " + table)
        self.service = NativeInputService(
            self.store,
            self.root / "native_input_journal.sqlite3",
            journal_factory=UnifiedJournal,
        )
        self.assertEqual(self.service.deliver_pending()["acknowledged"], 0)
        self.assertEqual(self.count(), 1)

    def test_migration_hold_survives_journal_only_crash(self):
        self.j.append(dict(message(), migration_hold=True), ORIGIN)
        self.service.close()
        self.service = NativeInputService(
            self.store,
            self.root / "native_input_journal.sqlite3",
            journal_factory=UnifiedJournal,
        )
        self.service.deliver_pending()
        self.assertEqual(self.service.inbox.counts(), {"MIGRATION_HELD": 1})

    def test_migration_resume_repeat_mapping_and_source_integrity(self):
        source = self.root / "legacy.sqlite3"
        old_database(source)
        before = checksum(source)
        migration = LegacyMigration(self.store, self.service, self.root)
        run = migration.create(source, {"old-a": asdict(ORIGIN)})
        self.assertEqual(run["total"], 5)
        migration.batch(run["id"], 1)
        self.assertEqual(self.service.inbox.counts(), {"MIGRATION_HELD": 1})
        migration = LegacyMigration(self.store, self.service, self.root)
        final = migration.batch(run["id"], 100)
        self.assertEqual(
            final["counts"],
            {"IMPORTED": 1, "ARCHIVED": 2, "INVALID_HASH": 1, "QUARANTINED": 1},
        )
        self.assertEqual(checksum(source), before)
        self.assertEqual(migration.batch(run["id"])["counts"], final["counts"])
        self.assertEqual(
            migration.create(source, {"old-a": asdict(ORIGIN)})["id"], run["id"]
        )
        migration.update_mapping(
            run["id"], {"old-b": asdict(replace(ORIGIN, user="43"))}
        )
        migration.batch(run["id"])
        self.assertEqual(migration.release(run["id"])["released"], 2)
        self.assertEqual(migration.release(run["id"])["released"], 0)

    def test_new_snapshot_same_source_imports_only_new_messages(self):
        source = self.root / "legacy.sqlite3"
        old_database(source)
        m = LegacyMigration(self.store, self.service, self.root)
        one = m.create(source, {"old-a": asdict(ORIGIN)})
        m.batch(one["id"])
        m.release(one["id"])
        before = self.count()
        with fixture_database(source) as db:
            text = "第二天去爬山。"
            db.execute(
                "INSERT INTO dl_capture VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    5,
                    "old-a",
                    "5",
                    text,
                    hashlib.sha256(text.encode()).hexdigest(),
                    "2026-11-16",
                    "pending",
                    None,
                    None,
                ),
            )
        two = m.create(source, {"old-a": asdict(ORIGIN)})
        self.assertNotEqual(one["id"], two["id"])
        m.batch(two["id"])
        self.assertEqual(self.count(), before + 1)

    def test_migration_identity_change_quarantined_and_mapping_immutable(self):
        source = self.root / "legacy.sqlite3"
        old_database(source)
        m = LegacyMigration(self.store, self.service, self.root)
        one = m.create(source, {"old-a": asdict(ORIGIN)})
        m.batch(one["id"])
        with fixture_database(source) as db:
            db.execute(
                "UPDATE dl_capture SET content=?,content_sha=? WHERE seq=1",
                ("新文本", hashlib.sha256("新文本".encode()).hexdigest()),
            )
        with self.assertRaises(ValueError):
            m.create(source, {"old-a": asdict(replace(ORIGIN, user="99"))})
        two = m.create(source, {"old-a": asdict(ORIGIN)})
        result = m.batch(two["id"])
        self.assertEqual(result["counts"]["IDENTITY_CONFLICT"], 1)

    def test_backup_receipt_mismatch_rejected(self):
        self.service.submit_message(message(), ORIGIN)
        gen = GenerationStore(self.root)
        backup = gen.create_backup({})
        path = self.root / "backups" / backup["id"]
        with fixture_database(path / "shadow.sqlite3") as db:
            db.execute("DELETE FROM native_input_inbox")
        manifest = read_json(path / "manifest.json")
        manifest["files"]["shadow.sqlite3"] = checksum(path / "shadow.sqlite3")
        atomic_json(path / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "receipt mismatch"):
            gen.validate_backup(backup["id"])

    def test_crash_before_and_after_pointer_switch_rolls_back(self):
        self.service.submit_message(message(), ORIGIN)
        gen = GenerationStore(self.root)
        backup = gen.create_backup({})
        for switch in (False, True):
            old = gen.current["directory"]
            op = gen.prepare_restore(backup["id"])
            if switch:
                gen.switch(op)
            gen = GenerationStore(self.root)
            self.assertEqual(gen.current["directory"], old)
            self.assertEqual(read_json(gen.operation)["phase"], "ROLLED_BACK")
            self.assertGreater(gen.current["epoch"], op["new"]["epoch"])

    def test_generation_and_path_validation(self):
        gen = GenerationStore(self.root)
        for path in ["../elsewhere", "generations/../evil", "/tmp/x"]:
            with self.assertRaises(ValueError):
                gen.directory(path)
        with self.assertRaises(ValueError):
            gen.validate_backup("../elsewhere")

    def test_migration_failed_receipt_resumes_at_last_commit(self):
        source = self.root / "legacy.sqlite3"
        old_database(source)
        migration = LegacyMigration(self.store, self.service, self.root)
        run = migration.create(
            source,
            {"old-a": asdict(ORIGIN), "old-b": asdict(replace(ORIGIN, user="43"))},
        )
        accept = self.service.inbox.accept
        count = 0

        def fail_second(record):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("injected inbox failure")
            return accept(record)

        with patch.object(self.service.inbox, "accept", fail_second):
            with self.assertRaises(RuntimeError):
                migration.batch(run["id"])
        failed = migration.status(run["id"])
        self.assertEqual((failed["watermark"], failed["state"]), (1, "FAILED"))
        self.assertEqual(migration.batch(run["id"])["state"], "READY")
        self.assertEqual(
            self.service.inbox.counts(), {"MIGRATION_HELD": 2, "ARCHIVED": 2}
        )

    def test_shadow_pair_import_preserves_source_identity(self):
        self.service.submit_message(message(), ORIGIN)
        other = GenerationStore(self.root / "other")
        imported = other.import_shadow(self.root)
        op = other.prepare_restore(imported["id"])
        other.switch(op)
        other.finish(op)
        store = V15Store(other.active / "shadow.sqlite3")
        service = NativeInputService(
            store,
            other.active / "native_input_journal.sqlite3",
            journal_factory=UnifiedJournal,
        )
        try:
            service.submit_message(message(), ORIGIN)
            self.assertEqual(service.inbox.counts(), {"QUEUED": 1})
        finally:
            service.close()
            store.close()

    def test_process_exit_during_restore_recovers_whole_generation(self):
        self.service.submit_message(message(), ORIGIN)
        gen = GenerationStore(self.root)
        backup = gen.create_backup({})
        code = """import sys,os
sys.path[:0]=[sys.argv[1],str(__import__('pathlib').Path(sys.argv[1]).parent)]
from astrbot_plugin_digital_life.application.storage import GenerationStore
g=GenerationStore(sys.argv[2]);op=g.prepare_restore(sys.argv[3])
if sys.argv[4]=='switch':g.switch(op)
os._exit(19)
"""
        for phase in ("prepare", "switch"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    code,
                    str(PACKAGE),
                    str(self.root),
                    backup["id"],
                    phase,
                ],
                timeout=10,
            )
            self.assertEqual(result.returncode, 19)
            recovered = GenerationStore(self.root)
            # Path.resolve() expands Windows 8.3 short names; compare the same form.
            self.assertEqual(recovered.active.resolve(), self.root.resolve())
            self.assertEqual(read_json(recovered.operation)["phase"], "ROLLED_BACK")

    def test_future_database_schema_is_rejected_before_switch(self):
        self.service.submit_message(message(), ORIGIN)
        gen = GenerationStore(self.root)
        backup = gen.create_backup({})
        base = self.root / "backups" / backup["id"]
        with fixture_database(base / "shadow.sqlite3") as db:
            db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        manifest = read_json(base / "manifest.json")
        manifest["files"]["shadow.sqlite3"] = checksum(base / "shadow.sqlite3")
        atomic_json(base / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "schema version"):
            gen.prepare_restore(backup["id"])
        self.assertFalse(gen.pointer.exists())

    def test_duplicate_snapshot_does_not_hold_already_released_work(self):
        source = self.root / "legacy.sqlite3"
        old_database(source)
        m = LegacyMigration(self.store, self.service, self.root)
        first = m.create(source, {"old-a": asdict(ORIGIN)})
        m.batch(first["id"])
        m.release(first["id"])
        with fixture_database(source) as db:
            db.execute("INSERT INTO summaries VALUES('第二份摘要')")
        second = m.create(source, {"old-a": asdict(ORIGIN)})
        result = m.batch(second["id"])
        self.assertEqual(result["counts"]["DUPLICATE"], 3)
        self.assertEqual(self.service.inbox.counts(), {"QUEUED": 1, "ARCHIVED": 2})

    def test_wrapped_history_text_is_held_instead_of_double_imported(self):
        self.service.submit_message(message(), ORIGIN)
        history = [{"role": "user", "content": [{"type": "text", "text": TEXT}]}]
        self.service.submit(history, ORIGIN)
        self.assertEqual((self.count(), self.j.pending()), (1, 1))


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"ASTRBOT_ROOT": self.tmp.name})
        self.env.start()
        self.context = Context()
        self.p = mod.DigitalLifePlugin(
            self.context,
            {"mode": "ASSIST", "allowed_sender_ids": "42", "proactive_enabled": True},
        )
        self.p._build_judge = lambda store: NativeRelationJudge(store, Provider())
        await self.p.initialize()
        await self.p.pump.stop()
        self.assertIsNotNone(self.p.store, self.p.error)

    async def asyncTearDown(self):
        await self.p.terminate()
        self.env.stop()
        self.tmp.cleanup()

    async def db(self, f):
        return await asyncio.get_running_loop().run_in_executor(self.p.pool, f)

    async def append(self, mid="m1", text=TEXT):
        await self.db(lambda: self.p._append(message(mid, text), ORIGIN))

    async def tick(self):
        return await self.p.pump.tick()

    def request(self, query=TEXT):
        return types.SimpleNamespace(
            prompt=query,
            system_prompt="原人格",
            contexts=[],
            extra_user_content_parts=[],
        )

    async def test_capture_without_saved_history_and_assist_output(self):
        await self.p.capture(Event())
        await self.p.pump.stop()
        await self.tick()
        req = self.request()
        await self.p.observe(Event(), req)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertTrue(req.extra_user_content_parts[0]._no_save)
        self.assertEqual(
            (req.prompt, req.system_prompt, req.contexts), (TEXT, "原人格", [])
        )
        self.assertEqual(sum((await self.p.status_async())["inbox"].values()), 1)

    async def test_backup_restore_reopens_both_databases_and_accepts_new_input(self):
        await self.append()
        await self.tick()
        req = OutputRequest(
            "before", ORIGIN.scope_id, TEXT, 1024, self.p.store_generation
        )
        output = await self.db(lambda: self.p.output_service.prepare(req))
        backup = await self.p.application.action("backup")
        await self.p.pump.stop()
        await self.append("m2", "周日去爬山。")
        result = await self.p.application.action("restore", {"id": backup["id"]})
        await self.p.pump.stop()
        self.assertTrue(result["restored"])
        self.assertGreater(self.p.store_generation, req.store_generation)
        self.assertFalse(
            await self.db(lambda: self.p.output_service.validate(output, req))
        )
        self.assertEqual(sum((await self.p.status_async())["inbox"].values()), 1)
        self.assertEqual(self.p.console_api.data_dir, self.p.data_dir)
        await self.append("m3", "周一去游泳。")
        await self.tick()
        self.assertEqual(sum((await self.p.status_async())["inbox"].values()), 2)
        fresh = self.request()
        self.assertEqual(
            (await self.p.inject_proactive(Event(), fresh, "restore-query"))[
                "output_status"
            ],
            "INJECTED",
        )

    async def test_restore_open_failure_rolls_back_running_generation(self):
        await self.append()
        backup = await self.p.application.action("backup")
        await self.p.pump.stop()
        original = self.p.application._open
        calls = []
        old = self.p.data_dir

        async def fail_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("injected open failure")
            await original()

        with patch.object(self.p.application, "_open", fail_once):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                await self.p.application.action("restore", {"id": backup["id"]})
        self.assertEqual(self.p.data_dir, old)
        self.assertFalse(self.p.application.busy)
        await self.p.pump.stop()
        await self.append("after-rollback")
        self.assertEqual(sum((await self.p.status_async())["inbox"].values()), 2)

    async def test_invalid_backup_does_not_close_runtime(self):
        store = self.p.store
        with self.assertRaises(ValueError):
            await self.p.application.action("restore", {"id": "../../bad"})
        self.assertIs(self.p.store, store)
        self.assertFalse(self.p.application.busy)

    async def test_canceled_caller_does_not_interrupt_backup(self):
        original = self.p.generations.create_backup
        entered = threading.Event()
        release = threading.Event()

        def delayed(config):
            entered.set()
            release.wait(3)
            return original(config)

        with patch.object(self.p.generations, "create_backup", delayed):
            task = asyncio.create_task(self.p.application.action("backup"))
            await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            await self.p.application.wait_idle()
        self.assertEqual(len(self.p.generations.backups()), 1)
        self.assertFalse(self.p.application.busy)

    async def test_withdraw_invalidates_evidence_and_replay(self):
        await self.append()
        await self.tick()
        seq = await self.db(
            lambda: self.p.store.db.execute(
                "SELECT source_seq FROM native_input_inbox"
            ).fetchone()[0]
        )
        await self.p.application.action(
            "withdraw", {"scope": ORIGIN.scope_id, "source_seq": seq}
        )
        await self.append()
        await self.tick()
        self.assertEqual((await self.p.status_async())["inbox"], {"WITHDRAWN": 1})
        found = await self.db(lambda: self.p.engine.retrieve(ORIGIN.scope_id, TEXT))
        self.assertEqual(found, [])

    async def test_tombstone_before_business_crash_is_reconciled(self):
        await self.append()
        await self.tick()
        key = await self.db(
            lambda: self.p.store.db.execute(
                "SELECT key FROM native_input_inbox"
            ).fetchone()[0]
        )
        await self.db(lambda: self.p.service.journal.tombstone(key, "fault-test"))
        await self.db(self.p._close)
        self.assertTrue(await self.db(self.p._ensure))
        self.assertEqual((await self.p.status_async())["inbox"], {"WITHDRAWN": 1})

    async def test_old_plugin_enabled_while_database_work_queued(self):
        entered = threading.Event()
        release = threading.Event()
        barrier = asyncio.create_task(self.db(lambda: (entered.set(), release.wait(3))))
        await asyncio.to_thread(entered.wait, 3)
        queued = asyncio.create_task(self.append())
        await asyncio.sleep(0)
        with patch.object(mod, "legacy_plugin_active", return_value=True):
            release.set()
            await barrier
            await queued
            self.assertEqual(await self.db(self.p.service.inbox.counts), {})
            self.assertTrue(self.p.ownership_blocked)

    async def test_readonly_management_rejects_write_and_routes_registered(self):
        self.assertIn(
            "/astrbot_plugin_digital_life/console/runtime/migration_create",
            self.context.routes,
        )
        self.p.console_read_only = True
        with self.assertRaises(ValueError):
            await self.p.application.action("backup")
        self.assertTrue(
            (await self.p.application.action("status"))["management_read_only"]
        )

    async def test_proactive_dedupe_isolated_no_capture_and_optout(self):
        await self.append()
        await self.tick()
        before = (await self.p.status_async())["inbox"]
        req = self.request()
        result = await self.p.inject_proactive(Event(), req, "scheduled-1")
        self.assertEqual(result["output_status"], "INJECTED")
        self.assertEqual(
            (await self.p.inject_proactive(Event(), req, "scheduled-1"))["status"],
            "DUPLICATE",
        )
        self.assertEqual(
            (
                await self.p.inject_proactive(
                    Event(), self.request("别的问题"), "scheduled-1"
                )
            )["status"],
            "CONFLICT",
        )
        self.assertEqual(
            (await self.p.inject_proactive(Event(user="99"), self.request(), "other"))[
                "status"
            ],
            "REJECTED",
        )
        self.assertEqual((await self.p.status_async())["inbox"], before)
        self.p.proactive_enabled = False
        self.assertEqual(
            (await self.p.inject_proactive(Event(), self.request(), "off"))["status"],
            "DISABLED",
        )

    async def test_followup_query_keeps_host_prompt_and_is_conversation_isolated(self):
        await self.append()
        await self.tick()
        req = self.request("那后来呢？")
        await self.p.observe(Event("m2", "那后来呢？"), req)
        self.assertEqual(req.prompt, "那后来呢？")
        resolved = await self.db(lambda: self.p.followup.resolve(ORIGIN, req.prompt))
        self.assertIn(TEXT, resolved)
        self.assertEqual(
            self.p.followup.resolve(replace(ORIGIN, conversation="other"), req.prompt),
            req.prompt,
        )

    async def test_maintenance_records_capture_gap_without_inserting(self):
        self.p.application.busy = True
        try:
            await self.p.capture(Event())
        finally:
            self.p.application.busy = False
        self.assertEqual((await self.p.status_async())["inbox"], {})
        self.assertEqual(
            read_json(self.p.base / "maintenance_gaps.json")["unresolved"]["count"], 1
        )

    async def test_preference_rank_only_reorders_admitted_candidates(self):
        await self.append("experience", "我昨天喝了红茶。")
        await self.tick()
        await self.append("preference", "我喜欢红茶。")
        await self.tick()

        def query():
            self.p.engine.preference_rank_enabled = True
            trace = self.p.engine.retrieve_with_audit(ORIGIN.scope_id, "红茶")
            self.assertEqual(len(trace["items"]), 2)
            self.assertIn("喜欢", trace["items"][0]["evidence"])
            self.assertTrue(
                all(
                    item["admission"]["verdict"] == "SUPPORTED"
                    for item in trace["items"]
                )
            )
            self.assertEqual(self.p.engine.retrieve(ORIGIN.scope_id, "红茶多少钱"), [])

        await self.db(query)

    async def test_proactive_budget_zero_and_persona_isolation(self):
        await self.append()
        await self.tick()
        self.context.conversation_manager.persona = "other-persona"
        req = self.request()
        result = await self.p.inject_proactive(Event(), req, "other-persona")
        self.assertEqual(result["output_status"], "EMPTY")
        self.assertEqual(req.extra_user_content_parts, [])
        self.context.conversation_manager.persona = "p1"
        self.p.memory_budget = 0
        result = await self.p.inject_proactive(Event(), self.request(), "zero-budget")
        self.assertEqual(result["output_status"], "EMPTY")

    async def test_waiting_owner_uses_current_generation_after_takeover(self):
        second = mod.DigitalLifePlugin(
            Context(), {"mode": "COLLECT", "allowed_sender_ids": "42"}
        )
        await second.initialize()
        try:
            self.assertTrue(second.ownership_blocked)
            await self.append()
            backup = await self.p.application.action("backup")
            await self.p.application.action("restore", {"id": backup["id"]})
            active = self.p.data_dir
            await self.p.terminate()
            self.assertTrue(await second._reconcile_ownership())
            self.assertEqual(second.data_dir, active)
            self.assertEqual(sum((await second.status_async())["inbox"].values()), 1)
        finally:
            await second.terminate()


class CompatibilityTests(unittest.TestCase):
    def test_followup_ttl_reset_and_scope(self):
        f = FollowupContext(ttl=1)
        f.remember(ORIGIN, message())
        self.assertIn(TEXT, f.resolve(ORIGIN, "那后来呢？"))
        self.assertEqual(
            f.resolve(replace(ORIGIN, user="43"), "那后来呢？"), "那后来呢？"
        )
        with patch(
            "astrbot_plugin_digital_life.application.compatibility.time.monotonic",
            return_value=10**12,
        ):
            self.assertEqual(f.resolve(ORIGIN, "那后来呢？"), "那后来呢？")
        f.remember(ORIGIN, message("reset", "换个话题"))
        self.assertEqual(f.resolve(ORIGIN, "那后来呢？"), "那后来呢？")


if __name__ == "__main__":
    unittest.main(verbosity=2)
