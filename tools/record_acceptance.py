"""Record the current acceptance result from the evidence the commands produced.

The record must never be hand-written: every number here comes from
``dist/regression.json``, ``dist/build_manifest.json``,
``dist/package_verification.json`` or the previous package in ``releases/``::

    python tools/run_regression.py
    python tools/build_unified_plugin.py --out-dir dist
    python tools/verify_unified_plugin.py --dist dist --report dist/package_verification.json
    python tools/record_acceptance.py
"""

from __future__ import annotations

import argparse
import json
import platform
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "digital-life-acceptance/1"

# Facts about the tree before this round; update only when a new baseline is set.
BASELINE = {
    "tests": 333,
    "skipped": 1,
    "skip_reason": "test_r0_bundle could not find the archived R0 bundle",
    "after": "the test moved to the historical test set and the active suite was extended",
}

NOT_VERIFIED = [
    "live AstrBot/NapCat instance (source import and archive inspection passed)",
    "Windows machine install",
    "browser UI operations",
    "migration on a real legacy database (a synthetic fixture covers the write path)",
    "72h soak",
    "power loss / fsync",
]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _zip_index(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--out", default="dev/reports/current_acceptance.json")
    parser.add_argument("--previous", default=None,
                        help="previous delivered package (default: releases/*.zip)")
    args = parser.parse_args(argv)

    dist = Path(args.dist)
    regression = _load(dist / "regression.json")
    build = _load(dist / "build_manifest.json")
    verification = _load(dist / "package_verification.json")
    export_path = dist / "plugin_export" / "SOURCE_MANIFEST.json"
    export = _load(export_path) if export_path.is_file() else None
    selftest_path = dist / "selftest.json"
    selftest = _load(selftest_path) if selftest_path.is_file() else None
    install_path = dist / "astrbot_install_check.json"
    install = _load(install_path) if install_path.is_file() else None

    previous = Path(args.previous) if args.previous else None
    if previous is None:
        candidates = sorted((REPO_ROOT / "releases").glob("*.zip"))
        previous = candidates[0] if len(candidates) == 1 else None

    record = {
        "schema": REPORT_SCHEMA,
        "tested_commit": build.get("source_commit"),
        "dirty": build.get("dirty"),
        "source_note": build.get("source_note"),
        "plugin_version": build["plugin_version"],
        "artifact_sha256": build["archive_sha256"],
        "command": regression["command"],
        "environment": {
            "os": platform.platform(),
            "python": regression["python"],
            "platform": regression["platform"],
            "astrbot": "not installed",
            "git": "not available in PATH for this run"
                   if build.get("source_commit") is None else "available",
        },
        "total": regression["total"],
        "passed": regression["passed"],
        "failed": regression["failed"],
        "errors": regression["errors"],
        "skipped": regression["skipped"],
        "skip_reasons": regression["skip_reasons"],
        "started_at": regression["started_at"],
        "finished_at": regression["finished_at"],
        "duration_seconds": regression["duration_seconds"],
        "log_reference": regression["log_reference"],
        "evidence": {
            "regression": "dist/regression.json",
            "build_manifest": "dist/build_manifest.json",
            "package_verification": "dist/package_verification.json",
            "export_manifest": "dist/plugin_export/SOURCE_MANIFEST.json",
            "negative_self_test": "dist/selftest.json",
            "astrbot_install_check": "dist/astrbot_install_check.json",
        },
        "package": {
            "archive": build["archive"],
            "bytes": build["archive_bytes"],
            "file_count": build["file_count"],
            "build_rules_version": build["build_rules_version"],
            "verification_ok": verification["ok"],
            "verified_checks": sum(1 for item in verification["checks"]
                                   if item["status"] == "pass"),
            "failed_checks": [item["id"] for item in verification["checks"]
                              if item["status"] == "fail"],
            "host": verification["host"],
            "host_path": verification.get("host_path"),
            "host_note": verification.get("host_note"),
            "astrbot_install_check": None if install is None else {
                "ok": install.get("ok"),
                "metadata_entry": install.get("metadata_entry"),
                "inspector": install.get("inspector"),
            },
            "negative_self_test": None if selftest is None else {
                "ok": selftest["ok"],
                "cases": [{"case": item["case"], "rejected": item["rejected"]}
                          for item in selftest["cases"]],
            },
        },
        "export": None if export is None else {
            "layout": export["layout"],
            "file_count": export["file_count"],
            "bundle_sha256": export["archive"]["sha256"],
        },
        "baseline_before_change": BASELINE,
        "not_verified": NOT_VERIFIED,
    }

    if previous is not None and previous.is_file():
        old = _zip_index(previous)
        new = _zip_index(dist / build["archive"])
        record["previous_delivery"] = {
            "archive": previous.name,
            "bytes": previous.stat().st_size,
            "file_count": len(old),
            "new_files": sorted(set(new) - set(old)),
            "removed_files": sorted(set(old) - set(new)),
            "changed_files": sorted(name for name in set(old) & set(new)
                                    if old[name] != new[name]),
        }
        import hashlib
        record["previous_delivery"]["sha256"] = hashlib.sha256(
            previous.read_bytes()).hexdigest()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    print(json.dumps({key: record[key] for key in
                      ("total", "passed", "failed", "errors", "skipped")},
                     ensure_ascii=False),
          "->", out.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
