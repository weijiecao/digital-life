"""Install/upgrade/rollback smoke over an extracted RC3 candidate.

The installer is intentionally a directory copy: this validates the package
layout and SQLite compatibility, not a platform-specific service install.
"""
from __future__ import annotations

import json
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def run_cli(root: Path, db: Path, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys;sys.path.insert(0,sys.argv.pop(1));"
         "from memory_engine.cli import main;raise SystemExit(main())",
         str(root), "--db", str(db), *args],
        cwd=root, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return {"returncode": proc.returncode, "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--previous-bundle', required=True, type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    previous = args.previous_bundle.resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from verify_r0_bundle import verify
    current_verified, previous_verified = verify(bundle), verify(previous)
    if current_verified['source_sha256'] == previous_verified['source_sha256']:
        raise ValueError('upgrade requires two different source bundles; reinstall is not upgrade')
    with tempfile.TemporaryDirectory(prefix="lifecycle-") as directory:
        work = Path(directory)
        v1, v2, rollback = work / "v1", work / "v2", work / "rollback"
        with zipfile.ZipFile(previous) as archive:
            archive.extractall(v1)
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(v2)
        db = work / "data.sqlite3"
        health = run_cli(v1, db, "health")
        ingest = run_cli(v1, db, "ingest", "--scope", "s", "--seq", "1",
                         "--text", "升级前的安排")
        upgrade = run_cli(v2, db, "health")
        upgrade_ingest = run_cli(v2, db, "ingest", "--scope", "s", "--seq", "2",
                                "--text", "升级后的安排")
        shutil.copytree(v1, rollback)
        rollback_health = run_cli(rollback, db, "health")
        rollback_search = run_cli(rollback, db, "search", "--scope", "s",
                                  "--query", "升级后的安排")
        rollback_old = run_cli(rollback, db, "search", "--scope", "s", "--query", "升级前的安排")
        old_id = json.loads(ingest['stdout']).get('event_id') if ingest['returncode'] == 0 else None
        new_id = json.loads(upgrade_ingest['stdout']).get('event_id') if upgrade_ingest['returncode'] == 0 else None
        old_returned = {i['event_id'] for i in json.loads(rollback_old['stdout'])} if rollback_old['returncode'] == 0 else set()
        new_returned = {i['event_id'] for i in json.loads(rollback_search['stdout'])} if rollback_search['returncode'] == 0 else set()
        checks = {
            "install_health": health["returncode"] == 0,
            "install_ingest": ingest["returncode"] == 0,
            "upgrade_health": upgrade["returncode"] == 0,
            "upgrade_ingest": upgrade_ingest["returncode"] == 0,
            "rollback_health": rollback_health["returncode"] == 0,
            "rollback_reads_old_and_new": old_id in old_returned and new_id in new_returned,
        }
        payload = {"pass": all(checks.values()), "checks": checks,
                   "bundle": str(bundle), "previous_bundle": str(previous),
                   "previous_source_sha256": previous_verified["source_sha256"],
                   "current_source_sha256": current_verified["source_sha256"],
                   "health": health,
                   "upgrade": upgrade, "rollback": rollback_health, "rollback_search": rollback_search}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
