"""Record the acceptance result from the reports the commands produced.

The record is evidence, never hand-written: every number comes from a report in
``dist/`` and every suite is cross-checked against the same commit, the same
version and the same candidate hash. A record is only ``ok`` when it is
*formal*: a clean commit, one artifact hash everywhere, and every required suite
present and passing::

    python tools/record_acceptance.py                      # formal record
    python tools/record_acceptance.py --allow-uncommitted  # diagnostic only
    python tools/record_acceptance.py --check-evidence dev/reports/acceptance/<sha>

Small reports and a final summary are copied to
``dev/reports/acceptance/<commit>/`` with an ``index.json`` of sizes and hashes,
so the evidence survives ``dist/`` being cleaned and can be re-verified later.
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

SCHEMA = "digital-life-acceptance/3"
EVIDENCE_SCHEMA = "digital-life-acceptance-evidence/2"
REQUIRED_PACKAGE_SUITES = {"completion", "core", "legacy"}
SUITE_IDS = {
    "completion": "completion",
    "core": "core-package",
    "legacy": "legacy-plugin-contracts",
}
REPORTS = {
    "regression": "regression.json",
    "package_regression": "package_regression.json",
    "build_manifest": "build_manifest.json",
    "package_verification": "package_verification.json",
    "negative_self_test": "selftest.json",
    "astrbot_install_check": "astrbot_install_check.json",
    "ui_dom": "ui_dom.json",
}


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def collect(dist: Path) -> dict:
    """Load every report that exists; missing ones are reported, not invented."""
    return {
        "regression": _load(dist / "regression.json"),
        "package": _load(dist / "package_regression.json"),
        "build": _load(dist / "build_manifest.json"),
        "verification": _load(dist / "package_verification.json"),
        "selftest": _load(dist / "selftest.json"),
        "install": _load(dist / "astrbot_install_check.json"),
        "ui_dom": _load(dist / "ui_dom.json"),
        "export": _load(dist / "plugin_export" / "SOURCE_MANIFEST.json"),
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


def _package_suite(suite: dict) -> dict:
    return {
        "id": SUITE_IDS.get(suite["id"], suite["id"]),
        "target": "package",
        "report": "dist/package_regression.json",
        "total": suite.get("total"),
        "passed": suite.get("passed"),
        "failed": suite.get("failed"),
        "errors": suite.get("errors"),
        "skipped": suite.get("skipped"),
        "ok": suite.get("ok"),
        "status": suite.get("status"),
    }


def _dom_suite(report: dict) -> dict:
    return {
        "id": "dom-wiring",
        "target": "package",
        "report": "dist/ui_dom.json",
        "total": len(report.get("operations", [])),
        "passed": len(report.get("operations", [])) if report.get("ok") else 0,
        "failed": 0 if report.get("ok") else 1,
        "errors": len(report.get("dom_errors", [])),
        "skipped": 0,
        "ok": report.get("ok"),
        "status": "passed" if report.get("ok") else "failed",
    }


def consistency(parts: dict, formal: bool) -> list[str]:
    """Cross-check every report against one commit, one version, one artifact."""
    problems: list[str] = []

    def need(name: str, value, detail: str) -> None:
        if value is None:
            problems.append("missing %s: %s" % (name, detail))

    build = parts.get("build")
    if build is None:
        return ["missing dist/build_manifest.json"]
    version = build.get("plugin_version")
    commit = build.get("source_commit")
    artifact = build.get("archive_sha256")
    try:
        expected_version = read_version(REPO_ROOT)
    except VersionError as exc:
        problems.append("VERSION unreadable: %s" % exc)
        expected_version = None
    if expected_version and version != expected_version:
        problems.append("build version %r != VERSION %r" % (version, expected_version))
    for item in version_check(REPO_ROOT)["problems"]:
        problems.append("version drift: %s" % item)

    if formal:
        if not isinstance(commit, str) or len(commit) != 40:
            problems.append("build has no 40-hex source commit (got %r)" % (commit,))
        if build.get("dirty") is not False:
            problems.append("build dirty=%r, expected False" % (build.get("dirty"),))
        artifact_path = REPO_ROOT / "dist" / str(build.get("archive"))
        if artifact_path.is_file():
            actual = sha256_file(artifact_path)
            if actual != artifact:
                problems.append("build manifest hash %r != artifact %r"
                                % ((artifact or "")[:12], actual[:12]))
        else:
            problems.append("candidate archive missing: %s" % artifact_path.name)

    regression = parts.get("regression")
    if regression is None:
        problems.append("missing dist/regression.json")
    else:
        if regression.get("total", 0) <= 0:
            problems.append("source regression ran zero tests")
        if not regression.get("ok"):
            problems.append("source regression failed")
        if formal and regression.get("tested_commit") != commit:
            problems.append("regression tested %r != build commit %r"
                            % (regression.get("tested_commit"), commit))
        if formal and regression.get("dirty") is not False:
            problems.append("regression dirty=%r" % (regression.get("dirty"),))

    package = parts.get("package")
    if package is None:
        problems.append("missing dist/package_regression.json (package suites)")
    else:
        if package.get("artifact_sha256") != artifact:
            problems.append("package suites ran on %r != artifact %r"
                            % ((package.get("artifact_sha256") or "")[:12],
                               (artifact or "")[:12]))
        if formal and package.get("tested_commit") != commit:
            problems.append("package suites tested %r != build commit %r"
                            % (package.get("tested_commit"), commit))
        if formal and package.get("dirty") is not False:
            problems.append("package suites dirty=%r" % (package.get("dirty"),))
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
        if not verification.get("checks"):
            problems.append("package verification has no checks")
        if formal and verification.get("source_commit") != commit:
            problems.append("verification is for %r" % verification.get("source_commit"))
        if formal and verification.get("plugin_version") != version:
            problems.append("verification version %r != %r"
                            % (verification.get("plugin_version"), version))
        if formal and verification.get("dirty") is not False:
            problems.append("verification dirty=%r" % (verification.get("dirty"),))

    if parts.get("selftest") is None:
        problems.append("missing dist/selftest.json (negative cases)")
    elif not parts["selftest"].get("ok"):
        problems.append("negative-case self test failed")

    export = parts.get("export")
    if export is None:
        problems.append("missing dist/plugin_export/SOURCE_MANIFEST.json")
    else:
        if formal and export.get("source_commit") != commit:
            problems.append("export is for %r" % export.get("source_commit"))
        if formal and export.get("plugin_version") != version:
            problems.append("export version %r != %r"
                            % (export.get("plugin_version"), version))
        if formal and export.get("dirty") is not False:
            problems.append("export dirty=%r" % (export.get("dirty"),))
        if export.get("archive", {}).get("sha256") != artifact:
            problems.append("export bundle hash differs from the candidate")
        if export.get("file_count") not in (None, build.get("file_count")):
            problems.append("export file count %r != package %r"
                            % (export.get("file_count"), build.get("file_count")))

    host = verification.get("host") if verification else None
    install = parts.get("install")
    if host == "astrbot-source":
        if install is None:
            problems.append("host import used AstrBot source but no archive "
                            "inspection report")
        elif not install.get("ok"):
            problems.append("AstrBot archive inspection failed")

    ui_dom = parts.get("ui_dom")
    if ui_dom is not None:
        if not ui_dom.get("ok"):
            problems.append("DOM wiring check failed")
        if ui_dom.get("visual_browser_validation") is not False:
            problems.append("DOM report must not claim browser validation")
        if formal and ui_dom.get("artifact_sha256") != artifact:
            problems.append("DOM check ran on %r" % (ui_dom.get("artifact_sha256") or "")[:12])
        if formal and ui_dom.get("source_commit") != commit:
            problems.append("DOM check is for %r" % ui_dom.get("source_commit"))
        if formal and ui_dom.get("dirty") is not False:
            problems.append("DOM check dirty=%r" % (ui_dom.get("dirty"),))
    return problems


def _previous(build: dict, release_dir: Path | None = None) -> Path | None:
    """Previous delivered package, excluding the current version/artifact."""
    version = build.get("plugin_version") or ""
    current = build.get("archive_sha256") or ""
    directory = release_dir if release_dir is not None else REPO_ROOT / "releases"
    candidates = []
    for path in sorted(directory.glob("*.zip")):
        if version and version in path.name:
            continue
        if sha256_file(path) == current:
            continue
        candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def build_record(dist: Path) -> dict:
    parts = collect(dist)
    build = parts["build"] or {}
    state = git_state(REPO_ROOT)
    formal = (build.get("source_commit") is not None
              and build.get("dirty") is False
              and state["commit"] == build.get("source_commit"))
    problems = consistency(parts, formal)
    if not formal:
        problems.append("not a clean commit: record cannot be formal")
    version = build.get("plugin_version")
    verification = parts.get("verification") or {}
    host = verification.get("host")

    suites = []
    if parts["regression"]:
        suites.append(_source_suite(parts["regression"]))
    for suite in (parts.get("package") or {}).get("suites", []):
        suites.append(_package_suite(suite))
    if parts.get("ui_dom"):
        suites.append(_dom_suite(parts["ui_dom"]))

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
            "astrbot": ("source checkout: %s" % verification.get("host_path")
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
        "ui_dom": ("passed" if (parts["ui_dom"] or {}).get("ok") else
                   "failed" if parts["ui_dom"] else "not-run"),
        "visual_browser": "not-run",
        "host_level": {"stub": "stub", "astrbot-source": "astrbot-source",
                       "astrbot": "instance", "none": "none"}.get(host, host),
        "package": {
            "verification_ok": verification.get("ok"),
            "verification_checks": len(verification.get("checks", [])),
            "host": host,
            "host_path": verification.get("host_path"),
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
            "ui_dom": "dist/ui_dom.json",
            "export_manifest": "dist/plugin_export/SOURCE_MANIFEST.json",
        },
        "problems": problems,
        "ok": formal and not problems,
    }
    previous = _previous(build)
    if previous is not None:
        record["previous_delivery"] = {
            "archive": previous.name,
            "bytes": previous.stat().st_size,
            "sha256": sha256_file(previous),
        }
    return record


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def persist_evidence(record: dict, dist: Path, evidence_root: Path) -> Path:
    """Write the summary, report copies and an index over the final contents."""
    tested = record.get("tested_commit") or "uncommitted"
    target = evidence_root / tested
    target.mkdir(parents=True, exist_ok=True)

    # Summary first, with the evidence path already fixed, so the index and the
    # summary cannot disagree about each other.
    summary = target / "summary.json"
    _write_json(summary, record)

    copied: list[str] = []
    for name in ("regression.json", "package_regression.json",
                 "build_manifest.json", "package_verification.json",
                 "selftest.json", "astrbot_install_check.json", "ui_dom.json"):
        source = dist / name
        if source.is_file():
            shutil.copy2(source, target / name)
            copied.append(name)
    export = dist / "plugin_export" / "SOURCE_MANIFEST.json"
    if export.is_file():
        shutil.copy2(export, target / "export_SOURCE_MANIFEST.json")
        copied.append("export_SOURCE_MANIFEST.json")

    files = {"summary.json": summary}
    for name in copied:
        files[name] = target / name
    index = {
        "schema": EVIDENCE_SCHEMA,
        "tested_commit": record.get("tested_commit"),
        "artifact_sha256": record.get("artifact_sha256"),
        "plugin_version": record.get("plugin_version"),
        "records": {name: {"bytes": path.stat().st_size,
                           "sha256": sha256_file(path)}
                    for name, path in sorted(files.items())},
    }
    _write_json(target / "index.json", index)
    return target


def check_evidence(directory: Path) -> dict:
    """Re-verify a persisted evidence directory against its own index."""
    index_path = directory / "index.json"
    if not index_path.is_file():
        return {"ok": False, "problems": ["missing index.json"]}
    index = json.loads(index_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if index.get("schema") != EVIDENCE_SCHEMA:
        problems.append("unexpected evidence schema %r" % index.get("schema"))
    for name, expected in index.get("records", {}).items():
        path = directory / name
        if not path.is_file():
            problems.append("missing %s" % name)
            continue
        if path.stat().st_size != expected.get("bytes"):
            problems.append("%s size mismatch" % name)
        if sha256_file(path) != expected.get("sha256"):
            problems.append("%s hash mismatch" % name)
    summary = directory / "summary.json"
    if summary.is_file():
        try:
            value = json.loads(summary.read_text(encoding="utf-8"))
        except ValueError:
            problems.append("summary.json is not valid JSON")
        else:
            if value.get("artifact_sha256") != index.get("artifact_sha256"):
                problems.append("summary artifact differs from index")
    else:
        problems.append("missing summary.json")
    return {"ok": not problems, "directory": str(directory),
            "records": len(index.get("records", {})), "problems": problems}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--out", default="dev/reports/current_acceptance.json")
    parser.add_argument("--evidence-dir", default="dev/reports/acceptance")
    parser.add_argument("--allow-uncommitted", action="store_true",
                        help="write a diagnostic record without a clean commit")
    parser.add_argument("--check-evidence", default=None,
                        help="re-verify an evidence directory and exit")
    args = parser.parse_args(argv)
    if args.check_evidence:
        result = check_evidence(Path(args.check_evidence))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    dist = Path(args.dist)
    if not dist.is_absolute():
        dist = REPO_ROOT / dist
    record = build_record(dist)
    if not record["formal"] and not args.allow_uncommitted:
        print(json.dumps({"ok": False, "error": "not a clean commit; use "
                                                "--allow-uncommitted for a "
                                                "diagnostic record",
                          "problems": record["problems"]}, ensure_ascii=False,
                         indent=2))
        return 2
    evidence = persist_evidence(record, dist, REPO_ROOT / args.evidence_dir)
    try:
        record["evidence_index"] = evidence.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        record["evidence_index"] = evidence.as_posix()
    # The summary must carry the final evidence path; rewrite it and the index
    # together so the two stay consistent.
    _write_json(evidence / "summary.json", record)
    index_path = evidence / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["records"]["summary.json"] = {
        "bytes": (evidence / "summary.json").stat().st_size,
        "sha256": sha256_file(evidence / "summary.json")}
    _write_json(index_path, index)

    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    _write_json(out, record)
    print(json.dumps({"ok": record["ok"], "formal": record["formal"],
                      "suites": [item["id"] for item in record["suites"]],
                      "problems": record["problems"],
                      "evidence_index": record["evidence_index"]},
                     ensure_ascii=False, indent=2))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
