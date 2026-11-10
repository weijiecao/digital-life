"""Small automatic backup scheduler for the M6 slice."""

from __future__ import annotations

import json
import time
from pathlib import Path


class BackupScheduler:
    def __init__(self, store, directory, interval_seconds: float = 3600,
                 keep: int = 3):
        self.store = store
        self.directory = Path(directory)
        self.interval_seconds = interval_seconds
        self.keep = max(1, keep)
        self.state_path = self.directory / ".backup-state.json"
        self.last_backup_at: float | None = None
        if self.state_path.exists():
            try:
                self.last_backup_at = json.loads(
                    self.state_path.read_text(encoding="utf-8")).get("last_backup_at")
            except (OSError, ValueError):
                self.last_backup_at = None

    def backups(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("backup-*.sqlite3"),
                      key=lambda p: p.stat().st_mtime)

    def maybe_backup(self, now: float | None = None) -> Path | None:
        now = time.time() if now is None else now
        if self.last_backup_at is not None and now - self.last_backup_at < self.interval_seconds:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / ("backup-%d.sqlite3" % int(now * 1000))
        self.store.backup_to(str(path))
        self.last_backup_at = now
        self.state_path.write_text(
            json.dumps({"last_backup_at": now}), encoding="utf-8")
        current = self.backups()
        for old in current[:-self.keep]:
            old.unlink(missing_ok=True)
        return path
