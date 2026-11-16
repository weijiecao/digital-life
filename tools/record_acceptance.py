"""Record the acceptance result from the reports the commands produced.

The record is evidence, never hand-written: every number comes from a report in
``dist/`` (or the release directory) and every suite is cross-checked to the same
source commit and the same candidate ZIP::

    python tools/run_regression.py
    python tools/build_unified_plugin.py --out-dir dist --require-clean
    python tools/verify_unified_plugin.py --dist dist --host-path ... --report ...
    python tools/run_package_regression.py --dist dist --report ...
    python tools/export_astrbot_plugin.py --output dist/plugin_export
    python tools/record_acceptance.py [--allow-uncommitted]

Small reports are copied to ``dev/reports/acceptance/<commit>/`` so the evidence
survives ``dist/`` being cleaned. ``ok`` is only true when the formal conditions
hold: a clean commit, one candidate hash everywhere, and every suite passing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.project_version import (VersionError, check as version_check,  # noqa: E402
                                   read_version)
from tools.repo_packaging import git_state, sha256_file  # noqa: E402

SCHEMA = "digital-life-acceptance/2"
REQUIRED_PACKAGE_SUITES = {"completion", "core", "legacy"}
SUITE_IDS = {
    "completion": "completion",
    "core": "core-package",
    "legacy": "legacy-plugin-contracts",
}
HOST_LEVELS = {"stub": "stub", "astrbot-source": "astrbot-source",
               "astrbot": "instance", "none": "none"}


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _suite_entry(suite: dict) -> dict:
    return {
        "id": SUITE_IDS.get(suite["id"], suite["id"]),
        "target": suite.get("target", "package"),
        "report": "dist/package_regression.json",
        "total": suite.get("total"),
        "passed": suite.get("passed"),
        "failed": suite.get("failed"),
        "errors": suite.get("errors"),
        "skipped": suite.get("skipped"),
        "ok": suite.get("ok"),
        "status": suite.get("status"),
    }


def _source_suite(regression: dict) -> dict:
    return {
        "id": "source-regression",
        "target": "source",
        "report": "dist/regression.json",
        "total": regression.get("total"),
        "passed": regression.get("passed"),
        "failed": regression.get("failed"),
        "errors": regression.get("errors"),
        "skipped": regression.get("skipped"),
        "ok": regression.get("ok"),
        "status": "passed" if regression.get("ok") else "failed",
    }


def collect(dist: Path) -> dict:
    """Load every report that exists; missing ones are reported, not invented."""
    return {
        "regression": _load(dist / "regression.json"),
        "package": _load(dist / "package_regression.json"),
        "build": _load(dist / "build_manifest.json"),
        "verification": _load(dist / "package_verification.json"),
        "selftest": _load(dist / "selftest.json"),
        "install": _load(dist / "astrbot_install_check.json"),
        "export": _load(dist / "plugin_export" / "SOURCE_MANIFEST.json"),
    }


def consistency(parts: dict, formal: bool) -> list[str]:
    problems: list[str] = []
    build = parts.get("build")
    if build is None:
        return ["missing dist/build_manifest.json"]
    version = build.get("plugin_version")
    source_commit = build.get("source_commit")
    artifact = build.get("archive_sha256")
    try:
        expected_version = read_version(REPO_ROOT)
    except VersionError as exc:
        problems.append("VERSION unreadable: %s" % exc)
        expected_version = None
    if expected_version and version != expected_version:
        problems.append("build version %r != VERSION %r" % (version, expected_version))
    version_state = version_check(REPO_ROOT)
    problems.extend("version drift: %s" % item for item in version_state["problems"])
    if formal:
        if source_commit is None:
            problems.append("build has no source commit (not a clean tree)")
        if build.get("dirty") is not False:
            problems.append("build reports dirty=%r" % build.get("dirty"))

    regression = parts.get("regression")
    if regression is None:
        problems.append("missing dist/regression.json")
    else:
        if regression.get("total", 0) <= 0:
            problems.append("source regression ran zero tests")
        if not regression.get("ok"):
            problems.append("source regression failed")
        if source_commit and regression.get("tested_commit") not in (None, source_commit):
            problems.append("regression tested %r but build is %r"
                            % (regression.get("tested_commit"), source_commit))

    package = parts.get("package")
    if package is None:
        problems.append("missing dist/package_regression.json (package suites)")
    else:
        if package.get("artifact_sha256") != artifact:
            problems.append("package suites ran on %r but build is %r"
                            % (package.get("artifact_sha256"), artifact))
        if source_commit and package.get("tested_commit") not in (None, source_commit):
            problems.append("package suites tested %r but build is %r"
                            % (package.get("tested_commit"), source_commit))
        ids = {suite.get("id") for suite in package.get("suites", [])}
        missing = sorted(REQUIRED_PACKAGE_SUITES - ids)
        if missing:
            problems.append("package suites missing: %s" % missing)
        for suite in package.get("suites", []):
            if not suite.get("ok"):
                problems.append("package suite %s failed (%s)"
                                % (suite.get("id"), suite.get("status")))
            if suite.get("total", 0) <= 0:
                problems.append("package suite %s ran zero tests" % suite.get("id"))

    verification = parts.get("verification")
    if verification is None:
        problems.append("missing dist/package_verification.json")
    else:
        if not verification.get("ok"):
            problems.append("package verification failed")
        if verification.get("source_commit") not in (None, source_commit):
            problems.append("verification is for %r" % verification.get("source_commit"))

    if parts.get("selftest") is None:
        problems.append("missing dist/selftest.json (negative cases)")
    elif not parts["selftest"].get("ok"):
        problems.append("negative-case self test failed")

    export = parts.get("export")
    if export is None:
        problems.append("missing dist/plugin_export/SOURCE_MANIFEST.json")
    else:
        if export.get("source_commit") not in (None, source_commit):
            problems.append("export is for %r" % export.get("source_commit"))
        if export.get("archive", {}).get("sha256") != artifact:
            problems.append("export bundle hash differs from the candidate")

    install = parts.get("install")
    host = verification.get("host") if verification else None
    if host == "astrbot-source" and install is None:
        problems.append("host import used AstrBot source but no archive inspection report")
    if install is not None and not install.get("ok"):
        problems.append("AstrBot archive inspection failed")
    return problems


def _previous(dist: Path) -> Path | None:
    candidates = sorted((REPO_ROOT / "releases").glob("*.zip"))
    return candidates[0] if len(candidates) == 1 else None


def build_record(dist: Path, include_previous: bool = True) -> dict:
    parts = collect(dist)
    build = parts["build"] or {}
    state = git_state(REPO_ROOT)
    formal = (build.get("source_commit") is not None
              and build.get("dirty") is False
              and state["commit"] == build.get("source_commit")
              and state["dirty"] is False)
    problems = consistency(parts, formal)
    version = build.get("plugin_version")
    host = (parts.get("verification") or {}).get("host")

    suites = []
    if parts["regression"]:
        suites.append(_source_suite(parts["regression"]))
    for suite in (parts.get("package") or {}).get("suites", []):
        suites.append(_suite_entry(suite))

    record = {
        "schema": SCHEMA,
        "tested_commit": build.get("source_commit"),
        "dirty": build.get("dirty"),
        "formal": formal,
        "plugin_version": version,
        "artifact_sha256": build.get("archive_sha256"),
        "artifact_bytes": build.get("archive_bytes"),
        "artifact_files": build.get("file_count"),
        "level": "dev" if version and "-dev." in version else
                 "rc" if version and "-rc." in version else "stable",
        "command": (parts["regression"] or {}).get("command",
                                                   "python tools/run_regression.py"),
        "environment": {
            "os": platform.platform(),
            "python": (parts["regression"] or {}).get("python"),
            "platform": (parts["regression"] or {}).get("platform"),
            "astrbot": ("source checkout: %s" % (parts["verification"] or {}).get("host_path")
                        if host == "astrbot-source" else
                        "not installed" if host in (None, "stub") else host),
            "git": "available" if state["commit"] else "not available",
        },
        "total": (parts["regression"] or {}).get("total"),
        "passed": (parts["regression"] or {}).get("passed"),
        "failed": (parts["regression"] or {}).get("failed"),
        "errors": (parts["regression"] or {}).get("errors"),
        "skipped": (parts["regression"] or {}).get("skipped"),
        "skip_reasons": (parts["regression"] or {}).get("skip_reasons", []),
        "started_at": (parts["regression"] or {}).get("started_at"),
        "finished_at": (parts["regression"] or {}).get("finished_at"),
        "duration_seconds": (parts["regression"] or {}).get("duration_seconds"),
        "suites": suites,
        "ui_dom": "not-run",
        "host_level": HOST_LEVELS.get(host, host),
        "package": {
            "verification_ok": (parts["verification"] or {}).get("ok"),
            "verification_checks": len((parts["verification"] or {}).get("checks", [])),
            "host": host,
            "host_path": (parts["verification"] or {}).get("host_path"),
            "negative_self_test": (parts["selftest"] or {}).get("ok"),
            "astrbot_install_check": (parts["install"] or {}).get("ok"),
            "export_layout": (parts["export"] or {}).get("layout"),
            "export_files": (parts["export"] or {}).get("file_count"),
        },
        "evidence": {
            "regression": "dist/regression.json",
            "package_regression": "dist/package_regression.json",
            "build_manifest": "dist/build_manifest.json",
            "package_verification": "dist/package_verification.json",
            "negative_self_test": "dist/selftest.json",
            "astrbot_install_check": "dist/astrbot_install_check.json",
            "export_manifest": "dist/plugin_export/SOURCE_MANIFEST.json",
        },
        "problems": problems,
        "ok": not problems,
    }
    if include_previous:
        previous = _previous(dist)
        if previous is not None:
            record["previous_delivery"] = {
                "archive": previous.name,
                "bytes": previous.stat().st_size,
                "sha256": sha256_file(previous),
            }
    return record


def persist_evidence(record: dict, dist: Path, evidence_root: Path) -> str:
    """Copy small reports next to the commit so they survive dist/."""
    tested = record.get("tested_commit") or "uncommitted"
    target = evidence_root / tested
    target.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    sources = {
        "regression.json": dist / "regression.json",
        "package_regression.json": dist / "package_regression.json",
        "build_manifest.json": dist / "build_manifest.json",
        "package_verification.json": dist / "package_verification.json",
        "selftest.json": dist / "selftest.json",
        "astrbot_install_check.json": dist / "astrbot_install_check.json",
        "export_SOURCE_MANIFEST.json": dist / "plugin_export" / "SOURCE_MANIFEST.json",
    }
    for name, source in sources.items():
        if not source.is_file():
            continue
        destination = target / name
        shutil.copy2(source, destination)
        index[name] = {"bytes": destination.stat().st_size,
                       "sha256": sha256_file(destination)}
    summary = target / "summary.json"
    summary.write_text(json.dumps(record, ensure_ascii=False, indent=2,
                                  sort_keys=True) + "\n",
                       encoding="utf-8", newline="\n")
    index["summary.json"] = {"bytes": summary.stat().st_size,
                             "sha256": sha256_file(summary)}
    (target / "index.json").write_text(
        json.dumps({"schema": "digital-life-acceptance-evidence/1",
                    "tested_commit": record.get("tested_commit"),
                    "artifact_sha256": record.get("artifact_sha256"),
                    "files": index}, ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    return target.relative_to(REPO_ROOT).as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--out", default="dev/reports/current_acceptance.json")
    parser.add_argument("--evidence-dir", default="dev/reports/acceptance")
    parser.add_argument("--allow-uncommitted", action="store_true",
                        help="write a diagnostic record without a clean commit")
    args = parser.parse_args(argv)
    dist = Path(args.dist)
    if not dist.is_absolute():
        dist = REPO_ROOT / dist
    record = build_record(dist)
    if not record["formal"] and not args.allow_uncommitted:
        record["problems"].append(
            "not a clean commit: run from a clean clone or pass --allow-uncommitted")
        record["ok"] = False
    evidence = persist_evidence(record, dist, REPO_ROOT / args.evidence_dir)
    record["evidence_index"] = evidence
    summary_path = REPO_ROOT / args.evidence_dir / (
        record.get("tested_commit") or "uncommitted") / "summary.json"
    summary_path.write_text(json.dumps(record, ensure_ascii=False, indent=2,
                                       sort_keys=True) + "\n",
                            encoding="utf-8", newline="\n")
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n",
                   encoding="utf-8", newline="\n")
    print(json.dumps({"ok": record["ok"], "formal": record["formal"],
                      "suites": [item["id"] for item in record["suites"]],
                      "problems": record["problems"],
                      "evidence_index": evidence}, ensure_ascii=False, indent=2))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
