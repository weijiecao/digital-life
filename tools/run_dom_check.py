"""Run the console DOM wiring check against the built candidate.

Requires Node and the locked jsdom dependency (``npm ci`` inside
``verification/plugin``). The result is written as a small report bound to the
same version, commit and artifact hash as the other suites::

    python tools/run_dom_check.py --dist dist --report dist/ui_dom.json

This is a jsdom wiring check, not a browser test; the report always states
``visual_browser_validation: false``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import REPO_ROOT, PackagingError, sha256_file  # noqa: E402

SCHEMA = "digital-life-ui-dom/1"
PLUGIN = "astrbot_plugin_digital_life"
DOM_CHECK = REPO_ROOT / "verification" / "plugin" / "console_dom_check.cjs"


def _env() -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(dist: Path, node: str | None = None) -> dict:
    manifest_path = dist / "build_manifest.json"
    if not manifest_path.is_file():
        raise PackagingError("no build manifest in %s" % dist)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate = dist / manifest["archive"]
    if not candidate.is_file():
        raise PackagingError("candidate package missing: %s" % candidate)
    if sha256_file(candidate) != manifest["archive_sha256"]:
        raise PackagingError("candidate does not match its manifest hash")
    if not (REPO_ROOT / "verification" / "plugin" / "node_modules" / "jsdom").is_dir():
        raise PackagingError("jsdom is not installed; run npm ci in verification/plugin")
    if not DOM_CHECK.is_file():
        raise PackagingError("missing %s" % DOM_CHECK)

    executable = node or os.environ.get("NODE_EXE") or "node"
    with tempfile.TemporaryDirectory(prefix="dl-dom-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(candidate) as archive:
            archive.extractall(root)
        package = root / PLUGIN
        completed = subprocess.run(
            [executable, str(DOM_CHECK), sys.executable, str(package)],
            cwd=str(REPO_ROOT / "verification" / "plugin"), env=_env(),
            capture_output=True, text=True, timeout=300)
        output = (completed.stdout or "").strip()
        payload = None
        if output:
            try:
                payload = json.loads(output[output.index("{"):])
            except ValueError:
                payload = None
        report = {
            "schema": SCHEMA,
            "ok": completed.returncode == 0 and bool(payload)
                  and bool(payload.get("operations")),
            "plugin_version": manifest.get("plugin_version"),
            "source_commit": manifest.get("source_commit"),
            "dirty": manifest.get("dirty"),
            "artifact_sha256": manifest["archive_sha256"],
            "environment": "jsdom + packaged ConsoleApi/ApplicationService; "
                           "AstrBot stub",
            "operations": (payload or {}).get("operations", []),
            "inbox": (payload or {}).get("inbox"),
            "generation": (payload or {}).get("generation"),
            "dom_errors": (payload or {}).get("dom_errors", []),
            "visual_browser_validation": False,
            "node": executable,
            "exit_code": completed.returncode,
            "stderr": (completed.stderr or "").strip()[:2000],
        }
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--report", default="dist/ui_dom.json")
    parser.add_argument("--node", default=None, help="node executable")
    args = parser.parse_args(argv)
    try:
        report = run(Path(args.dist), args.node)
    except PackagingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                 sort_keys=True) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "operations": report["operations"],
                      "visual_browser_validation": False}, ensure_ascii=False,
                     indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
