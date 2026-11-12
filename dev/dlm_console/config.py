"""定位 AstrBot 实例与数字记忆系统的数据目录。

一个 AstrBot 实例通常长这样::

    <root>/data/plugin_data/astrbot_plugin_digital_memory_store/manual_test_memory.sqlite3
    <root>/data/plugin_data/astrbot_plugin_memory_shadow/shadow.sqlite3
    <root>/data/plugin_data/astrbot_plugin_memory_shadow/native_input_journal.sqlite3
    <root>/data/plugin_data/astrbot_plugin_memory_shadow/shadow_events.jsonl
    <root>/data/plugin_data/astrbot_plugin_digital_memory_store/latency.jsonl

控制台不创建、不迁移任何数据库；找不到的组件在页面上显示为“未发现”。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

STORE_PLUGIN = "astrbot_plugin_digital_memory_store"
SHADOW_PLUGIN = "astrbot_plugin_memory_shadow"

STORE_DB = "manual_test_memory.sqlite3"
SHADOW_DB = "shadow.sqlite3"
JOURNAL_DB = "native_input_journal.sqlite3"
SHADOW_LOG = "shadow_events.jsonl"
LATENCY_LOG = "latency.jsonl"


class ConfigError(ValueError):
    """实例或数据目录无法解析。"""


@dataclass(frozen=True)
class Paths:
    """控制台读取的所有位置。字段为 ``None`` 表示没有配置该组件。"""

    workspace: Path
    instance: Path | None
    plugin_data: Path | None
    store_dir: Path | None
    shadow_dir: Path | None
    token: str
    read_only: bool

    @property
    def store_db(self) -> Path | None:
        return self.store_dir / STORE_DB if self.store_dir else None

    @property
    def shadow_db(self) -> Path | None:
        return self.shadow_dir / SHADOW_DB if self.shadow_dir else None

    @property
    def journal_db(self) -> Path | None:
        return self.shadow_dir / JOURNAL_DB if self.shadow_dir else None

    @property
    def shadow_log(self) -> Path | None:
        return self.shadow_dir / SHADOW_LOG if self.shadow_dir else None

    @property
    def latency_log(self) -> Path | None:
        return self.store_dir / LATENCY_LOG if self.store_dir else None


def _existing_dir(value: str | os.PathLike | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


def _candidate_instances(workspace: Path, explicit: str | None) -> list[Path]:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_dir():
            raise ConfigError("实例目录不存在: %s" % path)
        return [path]
    candidates = []
    env_root = os.environ.get("ASTRBOT_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.append(workspace / "testbed" / "instance")
    candidates.append(workspace / "instance")
    return candidates


def resolve(workspace: str | os.PathLike, instance: str | None = None,
            store_dir: str | None = None,
            shadow_dir: str | None = None,
            token: str = "", read_only: bool = False) -> Paths:
    """解析数据位置。

    ``store_dir`` / ``shadow_dir`` 可以直接指向 ``plugin_data`` 下的插件目录，
    用于实例不在默认位置、或只想查看其中一侧数据的情况。
    """
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ConfigError("工作目录不存在: %s" % root)

    explicit_store = _existing_dir(store_dir)
    if store_dir and explicit_store is None:
        raise ConfigError("store 数据目录不存在: %s" % store_dir)
    explicit_shadow = _existing_dir(shadow_dir)
    if shadow_dir and explicit_shadow is None:
        raise ConfigError("shadow 数据目录不存在: %s" % shadow_dir)

    plugin_data: Path | None = None
    instance_root: Path | None = None
    for candidate in _candidate_instances(root, instance):
        data = candidate / "data" / "plugin_data"
        if data.is_dir():
            instance_root = candidate.resolve()
            plugin_data = data.resolve()
            break

    if plugin_data is None and not (explicit_store and explicit_shadow):
        # 允许显式给出两个插件目录而完全不依赖 AstrBot 实例。
        if not (explicit_store or explicit_shadow):
            if instance:
                raise ConfigError(
                    "实例下没有 data/plugin_data: %s" % instance)
            # 没有实例不阻断启动：页面显示“未发现数据”，并提示如何指定。

    resolved_store = explicit_store
    resolved_shadow = explicit_shadow
    if plugin_data is not None:
        if resolved_store is None:
            candidate = plugin_data / STORE_PLUGIN
            if candidate.is_dir():
                resolved_store = candidate.resolve()
        if resolved_shadow is None:
            candidate = plugin_data / SHADOW_PLUGIN
            if candidate.is_dir():
                resolved_shadow = candidate.resolve()

    return Paths(workspace=root, instance=instance_root, plugin_data=plugin_data,
                 store_dir=resolved_store, shadow_dir=resolved_shadow,
                 token=token, read_only=read_only)


def describe(paths: Paths) -> list[tuple[str, Path | None, str]]:
    """给页面和终端复用的数据位置清单。"""
    return [
        ("工作目录", paths.workspace, "dir"),
        ("AstrBot 实例", paths.instance, "dir"),
        ("plugin_data", paths.plugin_data, "dir"),
        ("数字记忆基座目录", paths.store_dir, "dir"),
        ("影子插件目录", paths.shadow_dir, "dir"),
        ("基座数据库", paths.store_db, "file"),
        ("影子数据库", paths.shadow_db, "file"),
        ("输入 journal", paths.journal_db, "file"),
        ("影子观察日志", paths.shadow_log, "file"),
        ("钩子延迟日志", paths.latency_log, "file"),
    ]
