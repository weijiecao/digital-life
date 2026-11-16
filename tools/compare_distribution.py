"""Compare a plugin export with a distribution repository, read-only.

Only files declared in ``SOURCE_MANIFEST.json`` are "managed"; everything else in
the repository is listed as unmanaged material so that a first sync never
mistakes README/LICENSE/tests for removable clutter::

    python tools/compare_distribution.py \\
        --export dist/plugin_export_engineering \\
        --repository /path/to/astrbot_plugin_digital_life \\
        --report dist/distribution_diff.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import REPO_ROOT  # noqa: E402

MANIFEST = "SOURCE_MANIFEST.json"


def _norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _index(root: Path) -> dict[str, tuple[str, str]]:
    """Map relative path -> (raw sha256, normalised sha256)."""
    files: dict[str, tuple[str, str]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(".git/"):
            continue
        data = path.read_bytes()
        files[relative] = (hashlib.sha256(data).hexdigest(),
                           hashlib.sha256(_norm(data)).hexdigest())
    return files


def compare(export: Path, repository: Path) -> dict:
    manifest_path = export / MANIFEST
    if not manifest_path.is_file():
        return {"ok": False, "schema": "digital-life-distribution-diff/1",
                "error": "no %s in %s" % (MANIFEST, export)}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    managed = [item["path"] for item in manifest.get("files", [])]
    exported = _index(export)
    repo = _index(repository)

    identical, eol_only, changed, missing = [], [], [], []
    for path in sorted(managed):
        if path not in repo:
            missing.append(path)
            continue
        export_raw, export_norm = exported.get(path, ("", ""))
        repo_raw, repo_norm = repo[path]
        if export_raw == repo_raw:
            identical.append(path)
        elif export_norm == repo_norm:
            eol_only.append(path)
        else:
            changed.append(path)

    unmanaged = sorted(set(repo) - set(managed))
    groups: dict[str, int] = {}
    for path in unmanaged:
        head = path.split("/", 1)[0]
        groups[head] = groups.get(head, 0) + 1

    return {
        "ok": True,
        "schema": "digital-life-distribution-diff/1",
        "export": export.as_posix(),
        "repository": repository.as_posix(),
        "layout": manifest.get("layout"),
        "plugin_version": manifest.get("plugin_version"),
        "source_commit": manifest.get("source_commit"),
        "managed": len(managed),
        "identical": len(identical),
        "line_ending_only": eol_only,
        "changed": changed,
        "missing_in_repository": missing,
        "unmanaged": len(unmanaged),
        "unmanaged_by_group": dict(sorted(groups.items())),
        "unmanaged_examples": unmanaged[:20],
        "manifest_only": sorted(set(exported) - set(managed) - {MANIFEST}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default="dist/plugin_export_engineering")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)
    export = Path(args.export)
    if not export.is_absolute():
        export = REPO_ROOT / export
    repository = Path(args.repository)
    if not repository.is_dir():
        print(json.dumps({"ok": False, "error": "no such repository: %s"
                                             % repository}, ensure_ascii=False))
        return 2
    result = compare(export, repository)
    if args.report:
        report = Path(args.report)
        if not report.is_absolute():
            report = REPO_ROOT / report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                     sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")
    summary = {key: result.get(key) for key in
               ("ok", "managed", "identical", "unmanaged", "error")}
    summary["line_ending_only"] = len(result.get("line_ending_only", []))
    summary["changed"] = len(result.get("changed", []))
    summary["missing_in_repository"] = len(result.get("missing_in_repository", []))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
