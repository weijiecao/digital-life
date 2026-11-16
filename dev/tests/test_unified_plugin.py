"""Specialised regression for the unified plugin (0.7.0-dev.2).

The old engineering package kept these checks in a separate tree that was never
run by the active suite, so regressions in migration, recovery, ownership and the
management API could only be found by hand. This module keeps the same scenarios
inside ``dev/tests`` so ``unittest discover`` covers them.

Scope: application layer, runtime ownership, migration preview and the
create/batch/release write path, generation backup/restore, the output v2
contract, the console API surface and the package metadata contract. Real
AstrBot/NapCat behaviour is still verified separately in ``testbed/`` (package
import against the AstrBot source and its archive inspector) and a live instance
is not claimed here.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEV))

from astrbot_memory_input.journal import ImportConflict  # noqa: E402
from astrbot_memory_input.native import Origin  # noqa: E402
from astrbot_memory_input.unified import UnifiedJournal  # noqa: E402
from astrbot_plugin_digital_life.application.migration import LegacyMigration  # noqa: E402
from astrbot_plugin_digital_life.application.storage import (  # noqa: E402
    GenerationStore, atomic_json, checksum, managed_database, read_json)
from astrbot_plugin_digital_life.runtime.ownership import (  # noqa: E402
    LEGACY_NAME, DataDirectoryLock, OwnershipError, legacy_plugin_active)
from memory_engine.native_host import NativeInputService  # noqa: E402
from memory_output.contracts import (  # noqa: E402
    SCHEMA_VERSION, ContextOutput, OutputRequest)
from v1_5_consolidator.store import V15Store  # noqa: E402

PLUGIN = DEV / "astrbot_plugin_digital_life"


def origin(conversation="c1", user="u1"):
    return Origin("qq", "bot1", user, "persona1", conversation)


def message(text, message_id=None, role="user", **extra):
    value = {"role": role, "content": text, "timestamp": "2026-11-15T10:00:00+08:00"}
    if message_id is not None:
        value["message_id"] = message_id
    value.update(extra)
    return value


def legacy_database(path):
    """A minimal legacy memory-base snapshot: captures plus linked messages."""
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(
            """
            CREATE TABLE dl_capture(
              seq INTEGER PRIMARY KEY, scope TEXT NOT NULL, content TEXT NOT NULL,
              recorded_at TEXT, state TEXT DEFAULT 'active', deleted_at TEXT,
              content_sha TEXT, linked_source TEXT);
            CREATE TABLE messages(id INTEGER NOT NULL, scope TEXT NOT NULL,
              content TEXT NOT NULL, recorded_at TEXT, speaker TEXT,
              PRIMARY KEY(scope, id));
            """)
        connection.execute(
            "INSERT INTO dl_capture(seq,scope,content,recorded_at) VALUES(1,'s1','第一句话','t1')")
        connection.execute(
            "INSERT INTO dl_capture(seq,scope,content,recorded_at) VALUES(2,'s1','第二句话','t2')")
        connection.execute(
            "INSERT INTO dl_capture(seq,scope,content,recorded_at,state) "
            "VALUES(3,'s2','已撤回','t3','withdrawn')")
        connection.execute(
            "INSERT INTO messages(id,scope,content,recorded_at,speaker) "
            "VALUES(10,'s1','旧历史','t0','assistant')")
        connection.commit()
    finally:
        connection.close()


def empty_database(path, statements):
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(statements)
        connection.commit()
    finally:
        connection.close()


class OwnershipTests(unittest.TestCase):
    """One data directory has one owner; a loaded legacy Star blocks capture."""

    def test_lock_is_exclusive_inside_one_process(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = DataDirectoryLock(base)
            first.acquire()
            try:
                with self.assertRaises(OwnershipError):
                    DataDirectoryLock(base).acquire()
            finally:
                first.release()
            payload = json.loads((base / ".digital_life.owner").read_text(
                encoding="utf-8"))
            self.assertEqual(payload["owner"], "astrbot_plugin_digital_life")
            self.assertEqual(payload["pid"], os.getpid())
            second = DataDirectoryLock(base)
            second.acquire()
            second.release()

    def test_registry_reports_an_active_legacy_star(self):
        active = types.SimpleNamespace(name=LEGACY_NAME, activated=True)
        disabled = types.SimpleNamespace(name=LEGACY_NAME, activated=False)

        class Context:
            def __init__(self, stars):
                self.stars = stars

            def get_all_stars(self):
                return self.stars

        self.assertTrue(legacy_plugin_active(Context({"old": active})))
        self.assertFalse(legacy_plugin_active(Context({"old": disabled})))
        self.assertFalse(legacy_plugin_active(Context({})))

    def test_registry_answer_wins_over_loaded_module(self):
        class Context:
            def get_registered_star(self, name):
                return None

        self.assertFalse(legacy_plugin_active(Context()))

    def test_manager_fallback_detects_loaded_plugin(self):
        class Manager:
            def __init__(self, plugins):
                self.plugins = plugins

        context = types.SimpleNamespace(plugin_manager=Manager({LEGACY_NAME: object()}))
        self.assertTrue(legacy_plugin_active(context))
        quiet = types.SimpleNamespace(plugin_manager=Manager({}))
        self.assertFalse(legacy_plugin_active(quiet))

    def test_module_fallback_is_the_last_resort(self):
        name = "data.plugins.%s.main" % LEGACY_NAME
        sys.modules[name] = types.ModuleType(name)
        try:
            self.assertTrue(legacy_plugin_active(None))
        finally:
            sys.modules.pop(name, None)


class GenerationTests(unittest.TestCase):
    """Backups are verifiable, restores switch generations atomically."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "data"
        self.root.mkdir()
        empty_database(self.root / "shadow.sqlite3", """
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta VALUES('schema_version','1');
            CREATE TABLE native_input_inbox(
              key TEXT PRIMARY KEY, content_hash TEXT, scope_id TEXT,
              payload TEXT, state TEXT);
            CREATE TABLE source_ref(scope_id TEXT, seq INTEGER, sha TEXT);
            CREATE TABLE event_candidate(event_id TEXT, scope_id TEXT);
        """)
        empty_database(self.root / "native_input_journal.sqlite3", """
            CREATE TABLE native_stream(stream_id TEXT PRIMARY KEY);
            CREATE TABLE native_record(
              key TEXT PRIMARY KEY, stream_id TEXT, payload TEXT,
              delivered INTEGER NOT NULL DEFAULT 0);
        """)
        self.store = GenerationStore(self.root, recover=False)

    def tearDown(self):
        self.directory.cleanup()

    def test_backup_is_self_describing_and_verified(self):
        result = self.store.create_backup({"mode": "COLLECT"})
        self.assertEqual(result["generation"], 1)
        manifest = self.store.validate_backup(result["id"])
        self.assertEqual(manifest["format"], "digital-life-backup-v1")
        self.assertEqual(manifest["config"], {"mode": "COLLECT"})
        self.assertIn("shadow.sqlite3", manifest["files"])
        self.assertEqual(self.store.backups()[0]["id"], result["id"])

    def test_backup_rejects_tampered_bytes(self):
        result = self.store.create_backup({})
        target = self.root / "backups" / result["id"] / "shadow.sqlite3"
        target.write_bytes(target.read_bytes() + b"tampered")
        with self.assertRaises(ValueError):
            self.store.validate_backup(result["id"])

    def test_backup_rejects_an_unknown_id(self):
        with self.assertRaises(ValueError):
            self.store.validate_backup("../../etc/passwd")
        with self.assertRaises(ValueError):
            self.store.validate_backup("0" * 32)

    def test_restore_switches_generation_and_commits(self):
        backup = self.store.create_backup({})
        operation = self.store.prepare_restore(backup["id"])
        self.assertEqual(operation["phase"], "PREPARED")
        self.assertEqual(operation["new"]["epoch"], 2)
        self.store.switch(operation)
        self.assertEqual(self.store.current["epoch"], 2)
        self.assertTrue(self.store.active.is_dir())
        self.store.finish(operation)
        self.assertEqual(read_json(self.root / "restore_operation.json")["phase"],
                         "COMMITTED")

    def test_crash_after_switch_rolls_back_on_startup(self):
        backup = self.store.create_backup({})
        operation = self.store.prepare_restore(backup["id"])
        self.store.switch(operation)
        # Simulate a crash before the reopened database passed verification.
        recovered = GenerationStore(self.root, recover=True)
        self.assertEqual(recovered.current["directory"], ".")
        self.assertGreaterEqual(recovered.current["epoch"], 3)
        self.assertEqual(read_json(self.root / "restore_operation.json")["phase"],
                         "ROLLED_BACK")

    def test_generation_paths_cannot_escape(self):
        for name in ("generations/../../x", "generations/nothex"):
            with self.assertRaises(ValueError):
                self.store.directory(name)

    def test_managed_database_closes_on_success_and_failure(self):
        path = self.root / "lifecycle.sqlite3"
        with managed_database(str(path)) as connection:
            connection.execute("CREATE TABLE t(x INTEGER)")
        moved = self.root / "moved.sqlite3"
        os.replace(str(path), str(moved))  # Windows would fail with an open handle
        with self.assertRaises(RuntimeError):
            with managed_database(str(moved)) as connection:
                connection.execute("SELECT 1")
                raise RuntimeError("boom")
        os.replace(str(moved), str(path))


class MigrationPreviewTests(unittest.TestCase):
    """The legacy snapshot preview is the safety net before any write."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "legacy.sqlite3"
        legacy_database(self.path)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def test_inventory_counts_unlinked_messages(self):
        self.assertEqual(LegacyMigration.inventory(self.connection),
                         {"s1": 3, "s2": 1})

    def test_rows_report_identity_and_withdrawal(self):
        rows = LegacyMigration.rows(self.connection, 0, 10)
        identities = [row[0] for row in rows]
        self.assertEqual(identities[:3], ["capture:1", "capture:2", "capture:3"])
        self.assertTrue(rows[2][5], "withdrawn capture must be flagged as ignored")
        self.assertFalse(rows[3][5])
        self.assertEqual(rows[3][4], "assistant")

    def test_rows_paginate_by_watermark(self):
        first = LegacyMigration.rows(self.connection, 0, 2)
        second = LegacyMigration.rows(self.connection, 2, 2)
        self.assertEqual([row[0] for row in first], ["capture:1", "capture:2"])
        self.assertEqual([row[0] for row in second], ["capture:3", "message:s1:10"])

    def test_non_legacy_database_is_rejected(self):
        plain = Path(self.directory.name) / "plain.sqlite3"
        empty_database(plain, "CREATE TABLE other(x INTEGER)")
        connection = sqlite3.connect(str(plain))
        try:
            with self.assertRaises(ValueError):
                LegacyMigration.inventory(connection)
        finally:
            connection.close()


class RealtimeJournalTests(unittest.TestCase):
    """Stable IDs dedupe appends; withdrawals stop delivery."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = UnifiedJournal(Path(self.directory.name) / "journal.sqlite3")
        self.origin = origin()

    def tearDown(self):
        self.journal.close()
        self.directory.cleanup()

    def test_same_identity_appends_once(self):
        first = self.journal.append(message("我喜欢吃苹果", message_id="m1"), self.origin)
        second = self.journal.append(message("我喜欢吃苹果", message_id="m1"), self.origin)
        self.assertEqual(first["new"], 1)
        self.assertEqual(second, {"key": first["key"], "new": 0, "pending": 0})
        self.assertEqual(self.journal.db.execute(
            "SELECT COUNT(*) FROM input_canonical").fetchone()[0], 1)

    def test_changed_content_for_the_same_identity_conflicts(self):
        self.journal.append(message("原文", message_id="m1"), self.origin)
        with self.assertRaises(ImportConflict):
            self.journal.append(message("被改写", message_id="m1"), self.origin)

    def test_append_requires_a_stable_message_id(self):
        with self.assertRaises(ImportConflict):
            self.journal.append(message("没有 ID"), self.origin)

    def test_withdrawn_source_is_not_delivered(self):
        staged = self.journal.append(message("待撤回", message_id="m2"), self.origin)
        self.journal.tombstone(staged["key"], "operator_withdrawal")
        accepted = []

        class Sink:
            def accept(self, record):
                accepted.append(record.key)
                return {"key": record.key, "content_hash": record.content_hash}

        result = self.journal.deliver(Sink())
        self.assertEqual(result, {"acknowledged": 0, "remaining": 0})
        self.assertEqual(accepted, [])

    def test_history_without_identity_waits_when_realtime_overlaps(self):
        self.journal.append(message("同一句话", message_id="m9"), self.origin)
        staged = self.journal.stage([message("同一句话")], self.origin)
        self.assertEqual(staged["pending"], 1)
        self.assertEqual(self.journal.pending(), 1)


class OutputContractTests(unittest.TestCase):
    """The output protocol is v2 and refuses stale generations."""

    def test_schema_version_is_v2(self):
        self.assertEqual(SCHEMA_VERSION, "memory-output-v2")
        output = ContextOutput(request=OutputRequest("r1", "s1", "查询"), scope_epoch=1)
        self.assertEqual(output.schema_version, "memory-output-v2")
        self.assertFalse(output.persist)
        self.assertEqual(json.loads(output.to_json())["schema_version"],
                         "memory-output-v2")

    def test_generation_and_retrieval_query_are_validated(self):
        with self.assertRaises(ValueError):
            OutputRequest("r1", "s1", "查询", store_generation=-1)
        with self.assertRaises(ValueError):
            OutputRequest("r1", "s1", "查询", retrieval_query="   ")
        request = OutputRequest("r1", "s1", "查询", store_generation=3,
                                retrieval_query="改写后的查询")
        self.assertEqual((request.store_generation, request.retrieval_query),
                         (3, "改写后的查询"))


class ConsoleApiTests(unittest.TestCase):
    """The management page exposes status, maintenance and migration routes."""

    def setUp(self):
        self.registered = []
        self.saved = {name: sys.modules.get(name)
                      for name in ("astrbot", "astrbot.api", "astrbot.api.web")}
        astrbot = types.ModuleType("astrbot")
        api = types.ModuleType("astrbot.api")
        web = types.ModuleType("astrbot.api.web")
        web.json_response = lambda value: {"json": value}
        web.error_response = lambda message, status_code=500: {
            "error": message, "status": status_code}
        web.request = types.SimpleNamespace(json=lambda default=None: default,
                                            query={})
        astrbot.api = api
        api.web = web
        sys.modules.update({"astrbot": astrbot, "astrbot.api": api,
                            "astrbot.api.web": web})

    def tearDown(self):
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_routes_cover_management_and_migration(self):
        from astrbot_plugin_digital_life.console_api import ConsoleApi

        application = types.SimpleNamespace()
        console = ConsoleApi(dev=PLUGIN, instance=None, application=application)
        try:
            count = console.register(
                lambda path, handler, methods, description: self.registered.append(
                    (path, handler, tuple(methods))))
            self.assertEqual(count, len(self.registered))
            paths = [path for path, _, _ in self.registered]
            self.assertEqual(len(paths), len(set(paths)), "duplicate route path")
            base = "/astrbot_plugin_digital_life/console/"
            for suffix in ("status", "maintenance/backup", "maintenance/restore",
                           "maintenance/verify_backup", "runtime/migration_create",
                           "runtime/migration_batch", "runtime/migration_release",
                           "runtime/withdraw", "store/delete"):
                self.assertIn(base + suffix, paths)
            methods = {path: methods for path, _, methods in self.registered}
            self.assertEqual(methods[base + "status"], ("GET",))
            self.assertEqual(methods[base + "runtime/withdraw"], ("POST",))
        finally:
            console.close()

    def test_status_handler_reports_the_runtime_state(self):
        from astrbot_plugin_digital_life.console_api import ConsoleApi

        console = ConsoleApi(dev=PLUGIN, instance=None,
                             status_provider=lambda: {"lifecycle": "RUNNING"})
        try:
            console.register(lambda path, handler, methods, description: None)
            result = asyncio.run(console._status())
            self.assertEqual(result, {"json": {"lifecycle": "RUNNING"}})
        finally:
            console.close()


class PackageContractTests(unittest.TestCase):
    """Source metadata, configuration schema and UI assets stay consistent."""

    def test_metadata_declares_the_plugin(self):
        values = {}
        for line in (PLUGIN / "metadata.yaml").read_text(encoding="utf-8").splitlines():
            if ":" in line and not line.startswith((" ", "\t", "#")):
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip()
        self.assertEqual(values["name"], "astrbot_plugin_digital_life")
        self.assertTrue(values["version"])
        self.assertTrue(values.get("display_name"))

    def test_config_schema_entries_are_typed(self):
        schema = json.loads((PLUGIN / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertTrue(schema)
        for key, value in schema.items():
            self.assertIsInstance(value, dict, key)
            self.assertIn("type", value, key)

    def test_console_assets_exist_and_are_linked(self):
        for name in ("index.html", "app.js", "style.css"):
            path = PLUGIN / "views" / "console" / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 0, name)
        self.assertIn("app.js",
                      (PLUGIN / "views" / "console" / "index.html").read_text(
                          encoding="utf-8"))

    def test_store_generation_metadata_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            atomic_json(path, {"epoch": 2})
            self.assertEqual(read_json(path), {"epoch": 2})
            self.assertEqual(checksum(path), checksum(path))
            self.assertFalse(list(path.parent.glob("*.tmp")))


class MigrationWritePathTests(unittest.TestCase):
    """End-to-end migration: create -> batch -> resume -> release.

    The fixture is a synthetic legacy database (no private conversations); it
    covers the write path the preview tests cannot: inbox receipts, migration
    hold, release into the queue, restart continuation and idempotent repeat.
    """

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.legacy = base / "legacy.sqlite3"
        legacy_database(self.legacy)
        self.data = base / "data"
        self.data.mkdir()
        self.store = V15Store(self.data / "shadow.sqlite3")
        self.service = NativeInputService(
            self.store, self.data / "native_input_journal.sqlite3",
            journal_factory=UnifiedJournal)
        self.migration = LegacyMigration(self.store, self.service, self.data)

    def tearDown(self):
        self.service.journal.close()
        self.store.close()
        self.directory.cleanup()

    def _mapping(self):
        return {"s1": {"platform": "qq", "bot": "bot1", "user": "u1",
                       "persona": "persona1", "conversation": "legacy"}}

    def _inbox_state(self, key):
        row = self.store.db.execute(
            "SELECT state FROM native_input_inbox WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def test_full_migration_imports_holds_and_releases(self):
        run = self.migration.create(self.legacy, self._mapping(), source_id="legacy-a")
        self.assertEqual(run["state"], "SNAPSHOTTED")
        self.assertEqual(run["total"], 4)
        self.assertEqual(run["source_scopes"], {"s1": 3, "s2": 1})

        status = self.migration.batch(run["id"], limit=10)
        self.assertEqual(status["state"], "READY")
        self.assertEqual(status["counts"], {"ARCHIVED": 1, "IMPORTED": 2,
                                            "QUARANTINED": 1})
        self.assertEqual(status["remaining"], 0)

        # Imported user text waits in MIGRATION_HELD until release.
        held = self.store.db.execute(
            "SELECT COUNT(*) FROM native_input_inbox WHERE state='MIGRATION_HELD'"
        ).fetchone()[0]
        self.assertEqual(held, 2)

        released = self.migration.release(run["id"])
        self.assertEqual(released["released"], 2)
        queued = self.store.db.execute(
            "SELECT COUNT(*) FROM native_input_inbox WHERE state='QUEUED'"
        ).fetchone()[0]
        self.assertEqual(queued, 2)
        archived = self.store.db.execute(
            "SELECT COUNT(*) FROM native_input_inbox WHERE state='ARCHIVED'"
        ).fetchone()[0]
        self.assertEqual(archived, 1)

    def test_batch_resumes_after_interruption(self):
        run = self.migration.create(self.legacy, self._mapping(), source_id="legacy-b")
        first = self.migration.batch(run["id"], limit=1)
        self.assertEqual(first["state"], "IMPORTING")
        self.assertEqual(first["watermark"], 1)

        second = self.migration.batch(run["id"], limit=10)
        self.assertEqual(second["state"], "READY")
        self.assertGreaterEqual(second["watermark"], 4)
        self.assertEqual(second["counts"].get("IMPORTED"), 2)

    def test_repeat_batch_does_not_duplicate_receipts(self):
        run = self.migration.create(self.legacy, self._mapping(), source_id="legacy-c")
        self.migration.batch(run["id"], limit=10)
        before = self.store.db.execute(
            "SELECT COUNT(*) FROM native_input_inbox").fetchone()[0]
        status = self.migration.batch(run["id"], limit=10)
        after = self.store.db.execute(
            "SELECT COUNT(*) FROM native_input_inbox").fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(status["state"], "READY")
        self.assertEqual(status["counts"]["IMPORTED"], 2)

    def test_source_database_is_unchanged(self):
        before = checksum(self.legacy)
        run = self.migration.create(self.legacy, self._mapping(), source_id="legacy-d")
        self.migration.batch(run["id"], limit=10)
        self.migration.release(run["id"])
        self.assertEqual(checksum(self.legacy), before)
        snapshot = self.data / "migrations" / (
            self.migration._run(run["id"])["snapshot"])
        self.assertTrue(snapshot.is_file(), "frozen snapshot must be kept")

    def test_mapping_cannot_be_rebound_after_use(self):
        run = self.migration.create(self.legacy, self._mapping(), source_id="legacy-e")
        self.migration.batch(run["id"], limit=10)
        with self.assertRaises(ValueError):
            self.migration.update_mapping(run["id"], {
                "s1": {"platform": "qq", "bot": "bot1", "user": "someone-else",
                       "persona": "persona1", "conversation": "legacy"}})

    def test_unmapped_scopes_never_reach_the_queue(self):
        run = self.migration.create(self.legacy, {}, source_id="legacy-f")
        status = self.migration.batch(run["id"], limit=10)
        self.assertEqual(status["counts"].get("QUARANTINED"), 4)
        released = self.migration.release(run["id"])
        self.assertEqual(released["released"], 0)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM native_input_inbox").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
