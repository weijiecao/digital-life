"""Legacy unified-plugin contracts (20 cases) against the built package.

Adapted from the 0.7.0-dev.2 engineering package. The original file imported the
source tree and called the old builder, which would rebuild and overwrite the
candidate. Here the candidate package comes from ``DIGITAL_LIFE_CANDIDATE`` (or
``argv[1]``) and is staged into a temporary ``ROOT``, so every assertion runs
against the shipped ZIP while the original test bodies stay unchanged.

    python -m unittest verification.plugin.test_legacy_plugin_contracts
"""

from __future__ import annotations

import atexit
import asyncio
import io
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

_CANDIDATE = os.environ.get("DIGITAL_LIFE_CANDIDATE") or (
    sys.argv[1] if len(sys.argv) > 1 else "")
if not _CANDIDATE:
    raise RuntimeError("set DIGITAL_LIFE_CANDIDATE to the built plugin ZIP")
_CANDIDATE = Path(_CANDIDATE).resolve()
if not _CANDIDATE.is_file():
    raise RuntimeError("candidate package not found: %s" % _CANDIDATE)

ROOT = Path(tempfile.mkdtemp(prefix="dl-legacy-contracts-"))
atexit.register(shutil.rmtree, ROOT, True)
(ROOT / "dist").mkdir()
shutil.copy2(_CANDIDATE, ROOT / "dist" / _CANDIDATE.name)
with zipfile.ZipFile(_CANDIDATE) as _archive:
    _archive.extractall(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "astrbot_plugin_digital_life"))

from astrbot_plugin_digital_life import config  # noqa: E402
from astrbot_plugin_digital_life.console_api import ConsoleApi  # noqa: E402


class _CandidateBuild:
    """Stand-in for the old builder: reports the candidate instead of rebuilding."""

    def build(self):
        return {"archive": _CANDIDATE.name}


build_unified_plugin = _CandidateBuild()


def _install_astrbot_stubs():
    event = types.ModuleType("astrbot.api.event")

    class Filter:
        class EventMessageType:
            PRIVATE_MESSAGE = "PRIVATE_MESSAGE"

        @staticmethod
        def event_message_type(*args, **kwargs):
            return lambda func: func

        @staticmethod
        def on_llm_request(*args, **kwargs):
            return lambda func: func

    event.filter = Filter
    star = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context):
            self.context = context

    star.Star = Star
    star.Context = object
    web = types.ModuleType("astrbot.api.web")
    web.json_response = lambda data=None, **kwargs: data
    web.error_response = lambda message, **kwargs: {"error": message}
    web.request = types.SimpleNamespace(query={})
    modules = {
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": types.ModuleType("astrbot.api"),
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.api.web": web,
    }
    sys.modules.update(modules)


class ConfigTests(unittest.TestCase):
    def test_modes_and_assist_output(self):
        collect = config.resolve({"mode": "COLLECT",
                                  "allowed_sender_ids": "u1, u2"},
                                 {"ASTRBOT_ROOT": "."})
        assist = config.resolve({"mode": "ASSIST"}, {})
        self.assertEqual(collect.allowed_senders, ("u1", "u2"))
        self.assertEqual(collect.output_mode, "off")
        self.assertTrue(collect.capture_enabled)
        self.assertEqual(assist.output_mode, "inject")
        self.assertTrue(assist.injection_enabled)

    def test_empty_allowed_means_no_capture(self):
        cfg = config.resolve({"mode": "COLLECT", "allowed_sender_ids": ""}, {})
        self.assertEqual(cfg.allowed_senders, ())

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            config.resolve({"mode": "everything"}, {})

    def test_plugin_config_wins_over_env(self):
        cfg = config.resolve({"mode": "OFF"},
                             {"DIGITAL_LIFE_MODE": "ASSIST"})
        self.assertEqual(cfg.mode, "OFF")

    def test_collect_cannot_be_overridden_by_hidden_output_mode(self):
        cfg = config.resolve({"mode": "COLLECT",
                              "allowed_sender_ids": "42",
                              "output_mode": "inject"}, {})
        self.assertEqual(cfg.mode, "COLLECT")
        self.assertEqual(cfg.output_mode, "off")

    def test_explicit_empty_provider_disables_legacy_env(self):
        cfg = config.resolve({"judge_provider_id": ""},
                             {"SHADOW_JUDGE_PROVIDER_ID": "old-provider",
                              "SHADOW_JUDGE_PROVIDER": "openai"})
        self.assertEqual(cfg.judge_provider_id, "")
        self.assertEqual(cfg.judge_kind, "none")
        self.assertEqual(cfg.judge_source, "plugin")

    def test_missing_provider_key_can_use_legacy_env(self):
        cfg = config.resolve({}, {"SHADOW_JUDGE_PROVIDER_ID": "old-provider"})
        self.assertEqual(cfg.judge_provider_id, "old-provider")
        self.assertEqual(cfg.judge_kind, "astrbot")
        self.assertEqual(cfg.judge_source, "legacy-env")

    def test_schema_auto_provider_can_use_legacy_env(self):
        cfg = config.resolve({"judge_provider_id": "auto"},
                             {"SHADOW_JUDGE_PROVIDER_ID": "old-provider"})
        self.assertEqual(cfg.judge_provider_id, "old-provider")
        self.assertEqual(cfg.judge_source, "legacy-env")

    def test_host_budget_zero_means_auto(self):
        auto = config.resolve({"host_total_tokens": 0}, {})
        explicit = config.resolve({"host_total_tokens": 8192}, {})
        self.assertFalse(auto.host_total_explicit)
        self.assertTrue(explicit.host_total_explicit)


class ConsoleDataDirTests(unittest.TestCase):
    def test_console_binds_explicit_new_data_dir(self):
        with tempfile.TemporaryDirectory(prefix="console-dir-") as temp:
            new_dir = Path(temp) / "astrbot_plugin_digital_life"
            new_dir.mkdir()
            api = ConsoleApi(ROOT / "astrbot_plugin_digital_life", None,
                             data_dir=new_dir,
                             plugin_name="astrbot_plugin_digital_life")
            try:
                paths = api.console_instance().paths
                # Normalise Windows 8.3 short names on both sides.
                self.assertEqual(Path(paths.shadow_dir).resolve(), new_dir.resolve())
                self.assertIsNotNone(paths.shadow_db)
            finally:
                api.close()


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = build_unified_plugin.build()

    def test_package_is_single_self_contained_plugin(self):
        archive = ROOT / "dist" / self.manifest["archive"]
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
            roots = {name.split("/", 1)[0] for name in names}
            self.assertEqual(roots, {"astrbot_plugin_digital_life"})
            for required in ("astrbot_plugin_digital_life/main.py",
                             "astrbot_plugin_digital_life/config.py",
                             "astrbot_plugin_digital_life/_conf_schema.json",
                             "astrbot_plugin_digital_life/views/console/index.html",
                             "astrbot_plugin_digital_life/memory_engine/engine.py"):
                self.assertIn(required, names)
            metadata = handle.read(
                "astrbot_plugin_digital_life/metadata.yaml")
            self.assertFalse(metadata.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"name: astrbot_plugin_digital_life", metadata)
            self.assertNotIn(b"astrbot_plugin_memory_shadow", metadata)
            self.assertTrue(all(info.create_system == 0 for info in handle.infolist()))

    def test_import_and_lifecycle_off_mode(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-lifecycle-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            # build() already imported the bundled packages from its temporary
            # extraction; clear those module objects before importing this zip.
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")
                routes = []

                class Context:
                    conversation_manager = None

                    def register_web_api(self, route, handler, methods, desc):
                        routes.append(route)

                async def lifecycle():
                    await plugin.initialize()
                    self.assertEqual(plugin.status()["mode"], "OFF")
                    self.assertEqual(plugin.status()["lifecycle"], "RUNNING")
                    self.assertIsNone(plugin.pump)
                    self.assertTrue(any(route.endswith("/console/status")
                                        for route in routes))
                    await plugin.terminate()

                with patch.dict(os.environ, {"ASTRBOT_ROOT": str(Path(temp) / "instance")}):
                    plugin = module.DigitalLifePlugin(
                        Context(), {"mode": "OFF", "allowed_sender_ids": "u1"})
                    asyncio.run(lifecycle())
                self.assertEqual(plugin.status()["lifecycle"], "CLOSED")
            finally:
                sys.path.remove(str(package))

    def test_data_dir_selection_prefers_new_then_legacy(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-dirs-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")

                class Context:
                    conversation_manager = None

                    def register_web_api(self, route, handler, methods, desc):
                        pass

                root = Path(temp) / "instance"
                plugin_data = root / "data" / "plugin_data"
                legacy = plugin_data / "astrbot_plugin_memory_shadow"
                legacy.mkdir(parents=True)
                cfg = {"mode": "OFF", "allowed_sender_ids": "u1"}
                with patch.dict(os.environ, {"ASTRBOT_ROOT": str(root)}):
                    old = module.DigitalLifePlugin(Context(), cfg)
                    self.assertEqual(old.base, legacy.resolve())
                    self.assertTrue(old.legacy_data_dir)
                    asyncio.run(old.terminate())
                    fresh = plugin_data / "astrbot_plugin_digital_life"
                    fresh.mkdir()
                    current = module.DigitalLifePlugin(Context(), cfg)
                    self.assertEqual(current.base, fresh.resolve())
                    self.assertFalse(current.legacy_data_dir)
                    asyncio.run(current.terminate())
            finally:
                sys.path.remove(str(package))

    def test_commit_time_is_recorded_and_reloaded(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-commit-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")

                class Context:
                    conversation_manager = None

                    def register_web_api(self, route, handler, methods, desc):
                        pass

                cfg = {"mode": "OFF", "allowed_sender_ids": "u1"}
                with patch.dict(os.environ, {"ASTRBOT_ROOT": str(Path(temp) / "instance")}):
                    first = module.DigitalLifePlugin(Context(), cfg)
                    first._record_commit({"created": 1})
                    committed = first.last_commit_at
                    self.assertIsNotNone(committed)
                    asyncio.run(first.terminate())
                    second = module.DigitalLifePlugin(Context(), cfg)
                    self.assertEqual(second.last_commit_at, committed)
                    asyncio.run(second.terminate())
            finally:
                sys.path.remove(str(package))

    def test_registry_detection_and_ownership_recovery(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-registry-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")

                class Context:
                    conversation_manager = None

                    def __init__(self):
                        self.old_active = True

                    def get_all_stars(self):
                        return [types.SimpleNamespace(
                            name="astrbot_plugin_memory_shadow",
                            activated=self.old_active,
                            module_path=("data.plugins.astrbot_plugin_memory_shadow"
                                         ".main"))]

                    def get_registered_star(self, name):
                        if name == "astrbot_plugin_memory_shadow":
                            return self.get_all_stars()[0]
                        return None

                    def register_web_api(self, route, handler, methods, desc):
                        pass

                cfg = {"mode": "COLLECT", "allowed_sender_ids": "u1"}
                with patch.dict(os.environ, {"ASTRBOT_ROOT": str(Path(temp) / "instance")}):
                    context = Context()
                    plugin = module.DigitalLifePlugin(context, cfg)
                    self.assertEqual(plugin.status()["ownership"],
                                     "blocked-legacy")
                    asyncio.run(plugin.initialize())
                    self.assertIsNone(plugin.pump)

                    async def recover():
                        context.old_active = False
                        self.assertTrue(await plugin._reconcile_ownership())
                        self.assertEqual(plugin.status()["ownership"], "owned")
                        self.assertIsNotNone(plugin.store)
                        self.assertIsNotNone(plugin.pump)

                        context.old_active = True
                        self.assertFalse(await plugin._pump_guard())
                        await asyncio.sleep(0.05)
                        self.assertIsNone(plugin.pump)
                        self.assertFalse(await plugin._reconcile_ownership())
                        self.assertEqual(plugin.status()["ownership"],
                                         "blocked-legacy")
                        self.assertIsNone(plugin.pump)
                        self.assertIsNone(plugin.store)

                        context.old_active = False
                        self.assertTrue(await plugin._reconcile_ownership())
                        self.assertEqual(plugin.status()["ownership"], "owned")

                    asyncio.run(recover())
                    asyncio.run(plugin.terminate())
            finally:
                sys.path.remove(str(package))

    def test_qualified_legacy_module_fallback(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-module-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")
                name = "data.plugins.astrbot_plugin_memory_shadow.main"
                sys.modules[name] = types.ModuleType(name)
                try:
                    self.assertTrue(module.legacy_plugin_active(None))
                finally:
                    sys.modules.pop(name, None)
                self.assertFalse(module.legacy_plugin_active(None))
            finally:
                sys.path.remove(str(package))

    def test_inflight_model_result_is_not_committed_after_conflict(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-inflight-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")

                class Service:
                    def start_tick(self, *args):
                        return "scope"

                    def prepare_one(self, scope):
                        return "task"

                    def execute(self, task):
                        return task

                    def commit_one(self, task, allow_commit=None):
                        raise AssertionError("stale result must not be committed")

                calls = {"n": 0}

                async def guard():
                    calls["n"] += 1
                    return calls["n"] == 1

                pump = module.NativeInputPump(
                    Service(), guard=guard, commit_guard=lambda: True, limit=1)
                result = asyncio.run(pump.tick())
                self.assertEqual(result.get("skipped"), "guard_after_model")
                self.assertEqual(calls["n"], 2)
            finally:
                sys.path.remove(str(package))

    def test_status_queries_inbox_on_database_thread(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-status-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")

                class Context:
                    conversation_manager = None

                    def register_web_api(self, route, handler, methods, desc):
                        pass

                class FakeInbox:
                    def counts(self):
                        if threading.current_thread() is threading.main_thread():
                            raise AssertionError("inbox queried on event loop")
                        return {"QUEUED": 1}

                class FakeService:
                    inbox = FakeInbox()
                    stats = {"created": 1, "attached": 0}

                    def close(self):
                        pass

                with patch.dict(os.environ, {"ASTRBOT_ROOT": str(Path(temp) / "instance")}):
                    plugin = module.DigitalLifePlugin(
                        Context(), {"mode": "OFF", "allowed_sender_ids": "u1"})
                    plugin.service = FakeService()
                    snapshot = asyncio.run(plugin.status_async())
                    self.assertEqual(snapshot["inbox"], {"QUEUED": 1})
                    self.assertIsNotNone(snapshot["last_commit_observed_at"])
                    self.assertIsNone(snapshot["last_commit_at"])
                    asyncio.run(plugin.terminate())
            finally:
                sys.path.remove(str(package))

    def test_two_new_instances_cannot_own_same_data_dir(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-owner-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")

                class Context:
                    conversation_manager = None

                    def register_web_api(self, route, handler, methods, desc):
                        pass

                cfg = {"mode": "OFF", "allowed_sender_ids": "u1"}
                with patch.dict(os.environ, {"ASTRBOT_ROOT": str(Path(temp) / "instance")}):
                    first = module.DigitalLifePlugin(Context(), cfg)
                    second = module.DigitalLifePlugin(Context(), cfg)
                    self.assertEqual(first.status()["ownership"], "owned")
                    self.assertEqual(second.status()["ownership"], "blocked-locked")
                    asyncio.run(first.terminate())
                    asyncio.run(second.terminate())
                    third = module.DigitalLifePlugin(Context(), cfg)
                    self.assertEqual(third.status()["ownership"], "owned")
                    asyncio.run(third.terminate())
            finally:
                sys.path.remove(str(package))

    def test_legacy_plugin_module_blocks_capture_ownership(self):
        _install_astrbot_stubs()
        archive = ROOT / "dist" / self.manifest["archive"]
        with tempfile.TemporaryDirectory(prefix="unified-legacy-") as temp:
            with zipfile.ZipFile(archive) as handle:
                handle.extractall(temp)
            package = Path(temp) / "astrbot_plugin_digital_life"
            for name in list(sys.modules):
                if name.split(".", 1)[0] in {"memory_engine", "memory_output",
                                             "v1_5_consolidator",
                                             "astrbot_memory_input", "dlm_console",
                                             "main", "console_api", "config"}:
                    sys.modules.pop(name, None)
            sys.path.insert(0, str(package))
            try:
                module = __import__("main")

                class Context:
                    conversation_manager = None

                    def register_web_api(self, route, handler, methods, desc):
                        pass

                sys.modules["astrbot_plugin_memory_shadow"] = types.ModuleType(
                    "astrbot_plugin_memory_shadow")
                try:
                    with patch.dict(os.environ, {"ASTRBOT_ROOT": str(Path(temp) / "instance")}):
                        plugin = module.DigitalLifePlugin(
                            Context(), {"mode": "COLLECT",
                                        "allowed_sender_ids": "u1"})
                        self.assertEqual(plugin.status()["ownership"],
                                         "blocked-legacy")
                        asyncio.run(plugin.initialize())
                        self.assertIsNone(plugin.pump)
                        asyncio.run(plugin.terminate())
                finally:
                    sys.modules.pop("astrbot_plugin_memory_shadow", None)
            finally:
                sys.path.remove(str(package))


if __name__ == "__main__":
    unittest.main()
