"""Verify a built plugin archive against its build manifest.

The check is deliberately structural: member names are validated before anything
is extracted, the extract happens in a throwaway directory, and imports run in a
separate interpreter with ``PYTHONPATH`` cleared so a source checkout can never
mask a missing file inside the archive.

Without a real AstrBot installation the plugin module is imported against an
explicitly labelled host stub; that result is reported as ``host: stub`` and is
never presented as real-host acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import (PackagingError, build_environment,
                             check_members, dump_json, load_spec, sha256_file,
                             write_zip)

REPORT_SCHEMA = "digital-life-plugin-verification/1"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _check(checks: list[dict], ident: str, ok: bool, detail: str,
           skipped: bool = False) -> bool:
    checks.append({"id": ident,
                   "status": "skip" if skipped else ("pass" if ok else "fail"),
                   "detail": detail})
    return ok


def _python(code: str, cwd: Path, path_entries: list[Path],
            extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                          capture_output=True, text=True,
                          input=None, timeout=300)


def _stub_tree(root: Path) -> Path:
    """Create a minimal, clearly-labelled AstrBot stub for offline imports."""
    package = root / "astrbot"
    (package / "api").mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        '"""Offline host stub used only by tools/verify_unified_plugin.py."""\n',
        encoding="utf-8")
    (package / "api" / "__init__.py").write_text("", encoding="utf-8")
    (package / "api" / "event.py").write_text(
        "class EventMessageType:\n"
        "    PRIVATE_MESSAGE = 'private'\n"
        "    GROUP_MESSAGE = 'group'\n"
        "    ALL = 'all'\n"
        "\n\n"
        "class filter:  # noqa: N801 - mirrors the AstrBot API name\n"
        "    @staticmethod\n"
        "    def event_message_type(*args, **kwargs):\n"
        "        return lambda func: func\n"
        "\n"
        "    @staticmethod\n"
        "    def on_llm_request(*args, **kwargs):\n"
        "        return lambda func: func\n",
        encoding="utf-8")
    (package / "api" / "star.py").write_text(
        "class Context:\n"
        "    def __init__(self):\n"
        "        self._stars = {}\n"
        "\n"
        "    def get_all_stars(self):\n"
        "        return dict(self._stars)\n"
        "\n"
        "\n"
        "class Star:\n"
        "    def __init__(self, context=None, config=None):\n"
        "        self.context = context\n"
        "        self.config = config\n",
        encoding="utf-8")
    return root


def _host_checkout_has_astrbot(root: Path) -> bool:
    if not (root / "astrbot" / "api" / "star").exists() and not (
            root / "astrbot" / "api" / "star.py").exists():
        return False
    result = _python("import sys, astrbot.api.star\n", root,
                     [], )
    return result.returncode == 0


def _real_host_available(tmp: Path) -> bool:
    result = _python("import astrbot.api.star", tmp, [])
    return result.returncode == 0


def verify_bundle(bundle: Path, manifest_path: Path,
                  host_mode: str = "auto", host_path: str | None = None) -> dict:
    spec = load_spec()
    started = _now()
    checks: list[dict] = []
    bundle, manifest_path = Path(bundle), Path(manifest_path)
    report = {
        "schema": REPORT_SCHEMA,
        "bundle": bundle.name,
        "manifest": manifest_path.name,
        "plugin_version": None,
        "source_commit": None,
        "dirty": None,
        "host": "none",
        "ok": False,
        "checks": checks,
        "environment": build_environment(),
        "started_at": started,
        "finished_at": None,
    }

    if not bundle.is_file():
        _check(checks, "bundle-present", False, "bundle not found: %s" % bundle)
        report["finished_at"] = _now()
        return report
    if not manifest_path.is_file():
        _check(checks, "manifest-present", False,
               "manifest not found: %s" % manifest_path)
        report["finished_at"] = _now()
        return report

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required_fields = ("schema", "name", "plugin_version", "archive",
                       "archive_sha256", "archive_bytes", "file_count", "files")
    missing = [field for field in required_fields if field not in manifest]
    _check(checks, "manifest-schema", not missing,
           "missing fields: %s" % missing if missing else "manifest fields present")
    report["plugin_version"] = manifest.get("plugin_version")
    report["source_commit"] = manifest.get("source_commit")
    report["dirty"] = manifest.get("dirty")

    actual_sha = sha256_file(bundle)
    _check(checks, "bundle-sha256", actual_sha == manifest.get("archive_sha256"),
           "expected %s, found %s" % (manifest.get("archive_sha256"), actual_sha))
    _check(checks, "bundle-bytes", bundle.stat().st_size == manifest.get("archive_bytes"),
           "expected %s bytes, found %s"
           % (manifest.get("archive_bytes"), bundle.stat().st_size))

    with zipfile.ZipFile(bundle) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]

    members_ok = True
    try:
        check_members(names)
    except PackagingError as exc:
        members_ok = False
        _check(checks, "member-names", False, str(exc))
    if members_ok:
        _check(checks, "member-names", True, "%d members, no traversal" % len(names))

    links = [info.filename for info in infos
             if (info.external_attr >> 16) & 0o170000 == 0o120000]
    _check(checks, "member-types", not links,
           "symbolic links: %s" % links if links else "regular files only")
    _check(checks, "file-count", len(names) == manifest.get("file_count"),
           "manifest %s, archive %s" % (manifest.get("file_count"), len(names)))

    declared = {item["path"]: item for item in manifest.get("files", [])}
    unexpected = sorted(set(names) - set(declared))
    absent = sorted(set(declared) - set(names))
    _check(checks, "member-set", not unexpected and not absent,
           "unexpected=%s missing=%s" % (unexpected, absent))

    forbidden = [name for name in names
                 if name.endswith((".db", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
                                   ".key", ".pem"))
                 or Path(name).name in (".env", ".deepseek_key", "config.json")]
    _check(checks, "no-private-data", not forbidden,
           "forbidden members: %s" % forbidden if forbidden else
           "no databases, keys or local config")

    with tempfile.TemporaryDirectory(prefix="dl-verify-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(root)
        package = root / spec["top_level"]

        bad = []
        for name, item in declared.items():
            target = root / name
            if not target.is_file():
                bad.append((name, "missing"))
            elif sha256_file(target) != item["sha256"]:
                bad.append((name, "hash"))
        _check(checks, "file-hashes", not bad, "mismatches: %s" % bad if bad else
               "%d files match the manifest" % len(declared))

        absent_required = [rel for rel in spec["required"]
                           if not (package / rel).is_file()]
        _check(checks, "required-files", not absent_required,
               "missing: %s" % absent_required if absent_required else
               "%d required files present" % len(spec["required"]))

        metadata_ok, metadata_detail = _check_metadata(package, manifest)
        _check(checks, "metadata", metadata_ok, metadata_detail)

        schema_ok, schema_detail = _check_conf_schema(package)
        _check(checks, "conf-schema", schema_ok, schema_detail)

        ui_ok, ui_detail = _check_ui(package)
        _check(checks, "ui-assets", ui_ok, ui_detail)

        compile_result = _python(
            "import pathlib,sys\n"
            "bad=[]\n"
            "for path in pathlib.Path('.').rglob('*.py'):\n"
            "    try:\n"
            "        compile(path.read_text(encoding='utf-8'), str(path), 'exec')\n"
            "    except Exception as exc:\n"
            "        bad.append('%s: %s' % (path, exc))\n"
            "print('\\n'.join(bad))\n"
            "sys.exit(1 if bad else 0)\n", root, [])
        _check(checks, "py-compile", compile_result.returncode == 0,
               compile_result.stdout.strip() or "all modules compile")

        import_ok, import_detail = _check_imports(root, package, spec)
        _check(checks, "package-imports", import_ok, import_detail)

        host_result = _check_plugin_import(root, package, host_mode,
                                           spec["top_level"], host_path)
        _check(checks, "plugin-import", host_result[0], host_result[1],
               skipped=host_result[2])
        report["host"] = host_result[3]
        report["host_path"] = host_path
        report["host_note"] = {
            "stub": "offline import against a labelled host stub; "
                    "this is not real-host acceptance",
            "astrbot": "imported against the installed AstrBot",
            "astrbot-source": "imported against an AstrBot source checkout "
                              "(%s); still not a live instance" % host_path,
            "none": "plugin module import skipped",
        }[host_result[3]]

    report["ok"] = all(item["status"] != "fail" for item in checks)
    report["finished_at"] = _now()
    return report


def _check_metadata(package: Path, manifest: dict) -> tuple[bool, str]:
    path = package / "metadata.yaml"
    if not path.is_file():
        return False, "metadata.yaml missing"
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line and not line.startswith(("#", " ", "\t")):
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip("'\"")
    problems = []
    if not values.get("name"):
        problems.append("name missing")
    if values.get("version") != manifest.get("plugin_version"):
        problems.append("version %r != manifest %r"
                        % (values.get("version"), manifest.get("plugin_version")))
    if not values.get("display_name"):
        problems.append("display_name missing")
    return (not problems,
            "problems: %s" % problems if problems else
            "name=%s version=%s" % (values.get("name"), values.get("version")))


def _check_conf_schema(package: Path) -> tuple[bool, str]:
    path = package / "_conf_schema.json"
    if not path.is_file():
        return False, "_conf_schema.json missing"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return False, "invalid JSON: %s" % exc
    if not isinstance(schema, dict) or not schema:
        return False, "schema is not a non-empty object"
    invalid = [key for key, value in schema.items()
               if not isinstance(value, dict) or "type" not in value]
    return (not invalid,
            "entries without a type: %s" % invalid if invalid else
            "%d config entries" % len(schema))


def _check_ui(package: Path) -> tuple[bool, str]:
    problems = []
    for rel in ("views/console/index.html", "views/console/app.js",
                "views/console/style.css"):
        path = package / rel
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(rel)
    index = package / "views/console/index.html"
    if index.is_file() and "app.js" not in index.read_text(encoding="utf-8"):
        problems.append("index.html does not load app.js")
    return (not problems, "problems: %s" % problems if problems else
            "console assets present and linked")


def _check_imports(root: Path, package: Path, spec: dict) -> tuple[bool, str]:
    modules = list(spec["importable_packages"])
    modules += ["%s.%s" % (spec["top_level"], name)
                for name in spec["host_independent_modules"]]
    code = (
        "import importlib, json, sys\n"
        "sys.path.insert(0, %r)\n"
        "failed = {}\n"
        "for name in %r:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception as exc:\n"
        "        failed[name] = type(exc).__name__ + ': ' + str(exc)\n"
        "print(json.dumps(failed, ensure_ascii=False))\n"
        "sys.exit(1 if failed else 0)\n"
        % (str(package), modules))
    result = _python(code, root, [])
    if result.returncode == 0:
        return True, "%d packages imported" % len(modules)
    return False, (result.stdout.strip() or result.stderr.strip())[:2000]


def _check_plugin_import(root: Path, package: Path, host_mode: str,
                         top_level: str,
                         host_path: str | None = None
                         ) -> tuple[bool, str, bool, str]:
    if host_mode == "skip":
        return True, "plugin import skipped by request", True, "none"
    stub_root = None
    host_root = None
    host = "none"
    if host_path:
        host_root = Path(host_path).resolve()
        if not _host_checkout_has_astrbot(host_root):
            return (False, "host path does not contain an importable astrbot "
                           "package: %s" % host_path, False, "none")
        host = "astrbot-source"
    elif host_mode in ("auto", "stub"):
        if host_mode == "auto" and _real_host_available(root):
            host = "astrbot"
        else:
            stub_root = Path(tempfile.mkdtemp(prefix="dl-host-stub-"))
            _stub_tree(stub_root)
            host = "stub"
    host_entry = str(host_root) if host_root else (
        str(stub_root) if stub_root else "")
    host_mode_flag = "append" if host_root else "insert"
    code = (
        "import importlib, json, sys\n"
        "sys.path.insert(0, %r)\n"
        "if %r:\n"
        "    if %r == 'append':\n"
        "        sys.path.append(%r)\n"
        "    else:\n"
        "        sys.path.insert(0, %r)\n"
        "try:\n"
        "    module = importlib.import_module('%s')\n"
        "    print(json.dumps({'ok': True, 'file': getattr(module, '__file__', '')}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}))\n"
        "    sys.exit(1)\n"
        % (str(package), bool(host_entry), host_mode_flag, host_entry, host_entry,
           top_level))
    try:
        result = _python(code, root, [])
    finally:
        if stub_root is not None:
            shutil.rmtree(stub_root, ignore_errors=True)
    ok = result.returncode == 0
    detail = result.stdout.strip() or result.stderr.strip()
    if ok and host == "stub":
        detail += " (offline import against a labelled host stub)"
    elif ok:
        detail += " (imported against the installed AstrBot)"
    return ok, detail[:2000], False, host


# ------------------------------------------------------------------ self test
def _mutate_bundle(source: Path, target: Path, spec: dict, kind: str) -> None:
    import warnings

    with zipfile.ZipFile(source) as archive:
        payloads = [(info.filename, archive.read(info.filename))
                    for info in archive.infolist()]
    if kind == "missing":
        payloads = [item for item in payloads if not item[0].endswith("metadata.yaml")]
    elif kind == "tampered":
        payloads = [(name, data + b"\n# tampered\n" if name.endswith("main.py") else data)
                    for name, data in payloads]
    elif kind == "traversal":
        payloads = payloads + [("../escape.py", b"print('escape')\n")]
    elif kind == "duplicate":
        name, data = payloads[0]
        payloads = payloads + [(name, data)]
    else:
        raise ValueError("unknown mutation: %s" % kind)
    with warnings.catch_warnings():
        # A duplicate member is the point of this case; the zip writer warns.
        warnings.simplefilter("ignore", UserWarning)
        write_zip(target, payloads, spec)


def self_test(bundle: Path, manifest: Path) -> dict:
    spec = load_spec()
    results = []
    with tempfile.TemporaryDirectory(prefix="dl-verify-selftest-") as temp:
        root = Path(temp)
        baseline = verify_bundle(bundle, manifest, host_mode="skip")
        results.append({"case": "baseline", "rejected": False,
                        "ok": baseline["ok"],
                        "failed_checks": [c["id"] for c in baseline["checks"]
                                          if c["status"] == "fail"]})
        for kind in ("missing", "tampered", "traversal", "duplicate"):
            target = root / ("mutated-%s.zip" % kind)
            _mutate_bundle(bundle, target, spec, kind)
            report = verify_bundle(target, manifest, host_mode="skip")
            results.append({"case": kind, "rejected": not report["ok"],
                            "failed_checks": [c["id"] for c in report["checks"]
                                              if c["status"] == "fail"]})
    passed = results[0]["ok"] and all(item["rejected"] for item in results[1:])
    return {"schema": "digital-life-plugin-verification-selftest/1",
            "ok": passed, "cases": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", default=None, help="ZIP produced by the build")
    parser.add_argument("--dist", default="dist",
                        help="directory holding build_manifest.json when --bundle "
                             "is omitted")
    parser.add_argument("--report", default=None, help="write the JSON report here")
    parser.add_argument("--host", choices=("auto", "stub", "skip"), default="auto",
                        help="how to import the plugin module (default: auto)")
    parser.add_argument("--host-path", default=None,
                        help="path to an AstrBot source checkout (the directory "
                             "containing the astrbot package) to import against")
    parser.add_argument("--self-test", action="store_true",
                        help="also prove that mutated bundles are rejected")
    parser.add_argument("--self-test-report", default=None,
                        help="write the self-test result here as JSON")
    args = parser.parse_args(argv)

    if args.bundle:
        bundle = Path(args.bundle)
        manifest = bundle.parent / "build_manifest.json"
    else:
        manifest = Path(args.dist) / "build_manifest.json"
        if not manifest.is_file():
            print(json.dumps({"ok": False, "error": "no manifest in %s" % args.dist},
                             ensure_ascii=False))
            return 2
        bundle = manifest.parent / json.loads(
            manifest.read_text(encoding="utf-8"))["archive"]
    if args.self_test:
        result = self_test(bundle, manifest)
        if args.self_test_report:
            dump_json(Path(args.self_test_report), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    report = verify_bundle(bundle, manifest, args.host, args.host_path)
    if args.report:
        dump_json(Path(args.report), report)
    summary = {key: report[key] for key in
               ("ok", "bundle", "plugin_version", "host", "source_commit")}
    summary["failed"] = [item for item in report["checks"] if item["status"] == "fail"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
