"""Run package-level regression suites against one verified candidate ZIP.

The same ZIP is verified against its build manifest, extracted once into a
temporary directory, and then used by every selected suite:

* ``completion`` — 47 integration cases from the dev.2 engineering package
* ``core``       — 137 core-package cases from the 13 ``dev/tests`` sources
* ``legacy``     — 20 adapted unified-plugin contract cases

Suites run in fresh subprocesses with ``PYTHONPATH`` cleared, so nothing can
silently fall back to the source tree. A missing suite, zero cases, a timeout or
a module imported from outside the extracted package fails the run.

    python tools/run_package_regression.py --dist dist \\
        --suites completion,core,legacy \\
        --report dist/package_regression.json \\
        --log dist/package_regression.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import (REPO_ROOT, PackagingError, check_members,
                                  sha256_file)

SCHEMA = "digital-life-package-regression/1"
PLUGIN = "astrbot_plugin_digital_life"
SUITES = ("completion", "core", "legacy")
PACKAGES = ("astrbot_memory_input", "dlm_console", "memory_engine",
            "memory_output", "v1_5_consolidator")
RAN = re.compile(r"Ran (\d+) tests? in")
COUNTS = re.compile(r"(\w+)=(\d+)")


def _env(extra: dict) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "PYTHONHOME", "DIGITAL_LIFE_DEV")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def _parse(output: str) -> dict:
    total = 0
    match = RAN.search(output)
    if match:
        total = int(match.group(1))
    counts = {"failures": 0, "errors": 0, "skipped": 0}
    for line in output.splitlines():
        if line.startswith(("OK", "FAILED")):
            for key, value in COUNTS.findall(line):
                if key in counts:
                    counts[key] = int(value)
            break
    ok = bool(re.search(r"(?m)^OK\b", output)) and total > 0
    return {"total": total, "failed": counts["failures"],
            "errors": counts["errors"], "skipped": counts["skipped"],
            "passed": total - counts["failures"] - counts["errors"] - counts["skipped"],
            "ok": ok}


def _suite_command(suite: str, package: Path, candidate: Path) -> tuple[list[str], Path, dict]:
    plugin_dir = REPO_ROOT / "verification" / "plugin"
    if suite == "completion":
        return ([sys.executable, "-m", "unittest", "-q", "completion_suite"],
                plugin_dir, {"DIGITAL_LIFE_PACKAGE": str(package)})
    if suite == "legacy":
        return ([sys.executable, "-m", "unittest", "-q",
                 "test_legacy_plugin_contracts"],
                plugin_dir, {"DIGITAL_LIFE_CANDIDATE": str(candidate)})
    return ([sys.executable, str(REPO_ROOT / "verification" / "run_core_package.py")],
            REPO_ROOT, {"DIGITAL_LIFE_PACKAGE": str(package)})


def _import_check(package: Path) -> dict:
    code = (
        "import importlib, json, sys\n"
        "sys.path[:0] = [%r, %r]\n"
        "result = {}\n"
        "for name in %r:\n"
        "    module = importlib.import_module(name)\n"
        "    result[name] = getattr(module, '__file__', '')\n"
        "importlib.import_module('astrbot_plugin_digital_life')\n"
        "print(json.dumps(result))\n"
        % (str(package.parent), str(package), PACKAGES))
    completed = subprocess.run([sys.executable, "-c", code], cwd=str(package.parent),
                               env=_env({}), capture_output=True, text=True,
                               timeout=120)
    problems: list[str] = []
    origins = {}
    if completed.returncode != 0:
        problems.append((completed.stderr or completed.stdout).strip()[:500])
    else:
        origins = json.loads(completed.stdout)
        root = str(package.resolve()) + os.sep
        for name, origin in origins.items():
            if not origin or not str(Path(origin).resolve()).startswith(root):
                problems.append("%s loaded from %s" % (name, origin))
    return {"id": "package-imports", "ok": not problems,
            "detail": problems or "all package modules load from the extracted zip",
            "origins": origins}


def run(dist: Path, suites: list[str], report_path: Path, log_path: Path,
        timeout: float = 900.0) -> dict:
    manifest_path = dist / "build_manifest.json"
    if not manifest_path.is_file():
        raise PackagingError("no build manifest in %s" % dist)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = (dist / manifest["archive"]).resolve()
    if not candidate.is_file():
        raise PackagingError("candidate package missing: %s" % candidate)
    actual = sha256_file(candidate)
    if actual != manifest["archive_sha256"]:
        raise PackagingError("candidate sha256 %s != manifest %s"
                             % (actual, manifest["archive_sha256"]))

    result = {
        "schema": SCHEMA,
        "candidate": candidate.name,
        "artifact_sha256": actual,
        "tested_commit": manifest.get("source_commit"),
        "dirty": manifest.get("dirty"),
        "plugin_version": manifest.get("plugin_version"),
        "suites": [],
        "checks": [],
        "ok": False,
    }
    log_chunks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="dl-package-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(candidate) as archive:
            check_members(archive.namelist())
            archive.extractall(root)
        package = root / PLUGIN
        if not package.is_dir():
            raise PackagingError("candidate does not contain %s/" % PLUGIN)

        imports = _import_check(package)
        result["checks"].append(imports)
        log_chunks.append("[package-imports] %s\n%s"
                          % ("ok" if imports["ok"] else "FAILED",
                             json.dumps(imports["origins"], ensure_ascii=False,
                                        indent=2)))

        for suite in suites:
            command, cwd, extra = _suite_command(suite, package, candidate)
            started = time.perf_counter()
            entry = {"id": suite, "target": "package", "command": command,
                     "working_directory": str(cwd), "ok": False,
                     "status": "failed"}
            try:
                completed = subprocess.run(command, cwd=str(cwd),
                                           env=_env(extra), capture_output=True,
                                           text=True, timeout=timeout)
                output = (completed.stdout or "") + (completed.stderr or "")
                counts = _parse(output)
                entry.update(counts)
                entry["exit_code"] = completed.returncode
                entry["ok"] = counts["ok"] and completed.returncode == 0
                entry["status"] = ("passed" if entry["ok"] else
                                   "failed" if counts["total"] else "no-tests")
            except subprocess.TimeoutExpired:
                entry.update({"total": 0, "passed": 0, "failed": 0, "errors": 0,
                              "skipped": 0, "status": "timeout",
                              "detail": "suite exceeded %.0fs" % timeout})
                output = "[timeout] %s\n" % " ".join(command)
            entry["duration_seconds"] = round(time.perf_counter() - started, 3)
            result["suites"].append(entry)
            log_chunks.append("[%s] status=%s total=%s passed=%s failed=%s "
                              "errors=%s skipped=%s\n%s\n"
                              % (suite, entry["status"], entry.get("total"),
                                 entry.get("passed"), entry.get("failed"),
                                 entry.get("errors"), entry.get("skipped"), output))

    result["ok"] = (imports["ok"] and bool(result["suites"])
                    and all(item["ok"] for item in result["suites"]))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                      sort_keys=True) + "\n",
                           encoding="utf-8", newline="\n")
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(log_chunks), encoding="utf-8", newline="\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--suites", default="completion,core,legacy")
    parser.add_argument("--report", default="dist/package_regression.json")
    parser.add_argument("--log", default="dist/package_regression.log")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    selected = [item.strip() for item in args.suites.split(",") if item.strip()]
    unknown = [item for item in selected if item not in SUITES]
    if unknown:
        print(json.dumps({"ok": False, "error": "unknown suites: %s" % unknown},
                         ensure_ascii=False))
        return 2
    try:
        result = run(Path(args.dist), selected, Path(args.report), Path(args.log),
                     args.timeout)
    except PackagingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    summary = {"ok": result["ok"],
               "artifact": result["artifact_sha256"][:12],
               "suites": [{key: item.get(key) for key in
                           ("id", "status", "total", "passed", "failed", "errors",
                            "skipped")} for item in result["suites"]]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
