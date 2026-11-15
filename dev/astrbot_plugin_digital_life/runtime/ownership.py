"""Data-directory ownership for the unified plugin.

The lock prevents two unified instances from sharing one database. It cannot
detect the legacy plugin unless the legacy module is loaded in this process;
that is checked separately and blocks capture/injection instead of writing.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path


class OwnershipError(RuntimeError):
    pass


LEGACY_NAME = "astrbot_plugin_memory_shadow"


def _star_name(star):
    for attr in ("name", "star_name", "plugin_name"):
        value = getattr(star, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _star_module(star):
    for attr in ("module_path", "module", "module_name"):
        value = getattr(star, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _is_legacy_star(star) -> bool:
    if isinstance(star, str):
        return star == LEGACY_NAME or LEGACY_NAME in star.split(".")
    if _star_name(star) == LEGACY_NAME:
        return True
    module = _star_module(star)
    return LEGACY_NAME in module.split(".")


def _star_active(star) -> bool:
    for attr in ("activated", "active", "enabled"):
        value = getattr(star, attr, None)
        if value is not None:
            return bool(value)
    return True


def legacy_plugin_active(context=None) -> bool:
    """Detect the old shadow Star using AstrBot's registry first.

    AstrBot imports plugins as ``data.plugins.<name>.<module>``; a bare
    ``astrbot_plugin_memory_shadow`` module check is not sufficient. When the
    host exposes ``get_all_stars`` / ``get_registered_star``, their activation
    state is authoritative, so a residual disabled module is not treated as
    active. Module/manager fallbacks are used only when the registry API is
    unavailable.
    """
    if context is not None:
        all_stars = getattr(context, "get_all_stars", None)
        if callable(all_stars):
            try:
                stars = all_stars()
            except Exception:
                stars = None
            if stars is not None:
                if isinstance(stars, dict):
                    stars = list(stars.values())
                return any(_is_legacy_star(star) and _star_active(star)
                           for star in stars)
        registered = getattr(context, "get_registered_star", None)
        if callable(registered):
            try:
                star = registered(LEGACY_NAME)
            except Exception:
                star = None
            if star is not None:
                return _star_active(star)
            # The registry answered: a loaded-but-disabled module is not active.
            return False
        for attr in ("plugin_manager", "_plugin_manager", "star_manager",
                     "_star_manager"):
            manager = getattr(context, attr, None)
            if manager is None:
                continue
            for key in ("plugins", "_plugins", "loaded_plugins", "stars"):
                loaded = getattr(manager, key, None)
                if isinstance(loaded, dict):
                    for name, star in loaded.items():
                        if (str(name) == LEGACY_NAME
                                or _is_legacy_star(star)) and _star_active(star):
                            return True
                elif isinstance(loaded, (list, tuple, set)):
                    for star in loaded:
                        if _is_legacy_star(star) and _star_active(star):
                            return True
            getter = getattr(manager, "get_loaded_plugins", None)
            if callable(getter):
                try:
                    loaded = getter()
                except Exception:
                    loaded = None
                for star in loaded or []:
                    if _is_legacy_star(star) and _star_active(star):
                        return True
    # Last-resort fallback for hosts without a registry API.
    for name in list(sys.modules):
        if LEGACY_NAME in name.split("."):
            return True
    return False


class DataDirectoryLock:
    """A cross-process exclusive lock plus an in-process guard."""

    _guard = threading.Lock()
    _held: set[str] = set()

    def __init__(self, base: Path, owner: str = "astrbot_plugin_digital_life"):
        self.base = Path(base)
        self.owner = owner
        self.path = self.base / ".digital_life.owner"
        self.key = str(self.path.resolve())
        self.handle = None
        self.acquired = False

    def acquire(self) -> None:
        with self._guard:
            if self.key in self._held:
                raise OwnershipError("data directory already owned in this process")
            self._held.add(self.key)
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b")
            self.handle = handle
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            self._lock(handle)
            handle.seek(0)
            handle.truncate()
            payload = json.dumps({"owner": self.owner, "pid": os.getpid(),
                                  "acquired_at": time.time()},
                                 ensure_ascii=False).encode("utf-8")
            handle.write(payload)
            handle.flush()
            self.acquired = True
        except Exception as exc:
            self._release_handle()
            with self._guard:
                self._held.discard(self.key)
            if isinstance(exc, OwnershipError):
                raise
            raise OwnershipError("cannot lock %s: %s" % (self.path, exc)) from exc

    @staticmethod
    def _lock(handle) -> None:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise OwnershipError("data directory is locked by another process") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise OwnershipError("data directory is locked by another process") from exc

    @staticmethod
    def _unlock(handle) -> None:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass

    def _release_handle(self) -> None:
        handle, self.handle = self.handle, None
        self.acquired = False
        if handle is None:
            return
        try:
            self._unlock(handle)
        finally:
            try:
                handle.close()
            except Exception:
                pass

    def release(self) -> None:
        self._release_handle()
        with self._guard:
            self._held.discard(self.key)
