"""Export the built plugin content for the distribution repository.

The export is one-way: the main repository is the only source, and the exported
directory records the commit, the packaging rules and the archive hash it was
derived from. Nothing is pushed and no release is created.

Safety rules:

* a new or empty directory is always allowed and produces a reviewable diff;
* a directory that was written by this tool may be updated, but any file that
  the tool did not create blocks the export unless ``--force`` is given;
* ``--check`` never writes: it reports missing, modified and unexpected files.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import (REPO_ROOT, PackagingError, collect_entries,
                             git_state, load_attribute_rules, load_spec,
                             read_entries, read_version, sha256_bytes,
                             sha256_file, write_zip)

TOOL_NAME = "tools/export_astrbot_plugin.py"
TOOL_VERSION = "1"
MANIFEST_NAME = "SOURCE_MANIFEST.json"
SCHEMA = "digital-life-plugin-export/1"


def _payloads(spec: dict) -> list[tuple[str, bytes]]:
    entries = collect_entries(REPO_ROOT, spec)
    return read_entries(entries, load_attribute_rules(REPO_ROOT))


def _layout_path(target: str, layout: str, top_level: str,
                 shared: set[str]) -> str:
    """Map an archive member to the distribution repository layout.

    ``archive``     keeps the ZIP layout, byte-identical to the verified package.
    ``flat``        puts the plugin at the repository root (what a repository
                    install expects); not yet verified with a live host.
    ``engineering`` keeps the plugin folder at the root and the shared packages
                    under ``shared/``, which is what the existing distribution
                    repository uses today.
    """
    if layout == "archive":
        return target
    prefix = top_level + "/"
    if not target.startswith(prefix):
        raise PackagingError("cannot flatten member outside the plugin root: %s"
                            % target)
    relative = target[len(prefix):]
    if layout == "flat":
        return relative
    head = relative.split("/", 1)[0]
    if layout == "engineering" and head in shared:
        return "shared/" + relative
    return target


def bundle_identity(spec: dict, payloads: list[tuple[str, bytes]]) -> dict:
    """Hash the deterministic archive this export corresponds to."""
    with tempfile.TemporaryDirectory(prefix="dl-export-") as temp:
        archive = Path(temp) / "bundle.zip"
        write_zip(archive, payloads, spec)
        return {"name": "%s-%s.zip" % (spec["name"],
                                         read_version(REPO_ROOT, spec)),
                "sha256": sha256_file(archive),
                "bytes": archive.stat().st_size,
                "file_count": len(payloads)}


def build_manifest(spec: dict, layout: str) -> tuple[dict, dict[str, bytes]]:
    payloads = _payloads(spec)
    shared = {item["target"] for item in spec["shared_packages"]}
    files = {_layout_path(target, layout, spec["top_level"], shared): data
             for target, data in payloads}
    state = git_state(REPO_ROOT)
    manifest = {
        "schema": SCHEMA,
        "source_repository": "weijiecao/digital-life",
        "source_commit": state["commit"],
        "dirty": state["dirty"],
        "source_note": state["reason"],
        "plugin_version": read_version(REPO_ROOT, spec),
        "build_rules_version": spec["build_rules_version"],
        "layout": layout,
        "archive": bundle_identity(spec, payloads),
        "generated_by": {"tool": TOOL_NAME, "version": TOOL_VERSION},
        "file_count": len(files),
        "files": [{"path": path, "sha256": sha256_bytes(data), "bytes": len(data)}
                  for path, data in sorted(files.items())],
    }
    return manifest, files


def _previous_manifest(target: Path) -> dict | None:
    path = target / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return value if value.get("schema") == SCHEMA else None


def _existing_files(target: Path) -> set[str]:
    return {path.relative_to(target).as_posix()
            for path in target.rglob("*") if path.is_file()}


def _managed_path(output: Path, relative: str) -> Path:
    """Resolve a manifest path inside the export; reject escapes and links."""
    if not isinstance(relative, str) or not relative.strip():
        raise PackagingError("invalid managed path: %r" % relative)
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PackagingError("managed path escapes the export: %r" % relative)
    target = (output / candidate).resolve()
    root = output.resolve()
    if target != root and root not in target.parents:
        raise PackagingError("managed path escapes the export: %r" % relative)
    if target.is_symlink():
        raise PackagingError("managed path is a symbolic link: %r" % relative)
    return target


def _tampered_fields(recorded: dict, expected: dict) -> list[dict]:
    """Fields that must match exactly for an export to describe this source."""
    fields = ("schema", "source_repository", "source_commit", "dirty",
              "plugin_version", "build_rules_version", "layout", "file_count",
              "archive", "generated_by")
    problems = []
    for field in fields:
        if recorded.get(field) != expected.get(field):
            problems.append({"field": field, "recorded": recorded.get(field),
                             "expected": expected.get(field)})
    return problems


def export(output: Path, layout: str, force: bool = False,
           require_clean: bool = False) -> dict:
    spec = load_spec()
    if require_clean:
        state = git_state(REPO_ROOT)
        if state["commit"] is None:
            raise PackagingError("cannot prove a clean source: %s"
                                 % (state["reason"] or "unknown"))
        if state["dirty"]:
            raise PackagingError("refusing a formal export from a dirty tree")
    manifest, files = build_manifest(spec, layout)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2,
                                 sort_keys=True) + "\n").encode("utf-8")

    if output.exists() and any(output.iterdir()):
        previous = _previous_manifest(output)
        existing = _existing_files(output)
        if previous is None:
            raise PackagingError(
                "%s is not empty and has no %s; refusing to overwrite unknown "
                "content (use a fresh directory to review the diff)"
                % (output, MANIFEST_NAME))
        managed = []
        for item in previous.get("files", []):
            target = _managed_path(output, item.get("path"))
            managed.append((item, target))
        known = {item["path"] for item, _ in managed}
        drift = [item["path"] for item, target in managed
                 if target.is_file() and sha256_file(target) != item.get("sha256")]
        if drift and not force:
            raise PackagingError(
                "managed files changed since the last export: %s "
                "(review the diff, then use --force)" % drift[:8])
        unknown = sorted(existing - known - {MANIFEST_NAME})
        if unknown and not force:
            raise PackagingError(
                "refusing to overwrite files this tool did not create: %s "
                "(use --force after reviewing them)" % unknown[:8])
        stale = sorted(known - set(files) - {MANIFEST_NAME})
        for path in stale:
            _managed_path(output, path).unlink()
    output.mkdir(parents=True, exist_ok=True)

    for path, data in sorted(files.items()):
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (output / MANIFEST_NAME).write_bytes(manifest_bytes)
    return {"output": str(output), "files": len(files),
            "plugin_version": manifest["plugin_version"],
            "layout": layout, "bundle_sha256": manifest["archive"]["sha256"],
            "source_commit": manifest["source_commit"], "dirty": manifest["dirty"]}


def check(output: Path, layout: str) -> dict:
    spec = load_spec()
    manifest, files = build_manifest(spec, layout)
    problems = {"fields": [], "missing": [], "modified": [], "unexpected": []}
    if not output.is_dir():
        problems["missing"].append("<directory>")
        return {"ok": False, "output": str(output), "problems": problems}
    for path, data in sorted(files.items()):
        target = output / path
        if not target.is_file():
            problems["missing"].append(path)
        elif sha256_file(target) != sha256_bytes(data):
            problems["modified"].append(path)
    expected = set(files) | {MANIFEST_NAME}
    problems["unexpected"] = sorted(_existing_files(output) - expected)
    manifest_file = output / MANIFEST_NAME
    if not manifest_file.is_file():
        problems["missing"].append(MANIFEST_NAME)
    else:
        try:
            recorded = json.loads(manifest_file.read_text(encoding="utf-8"))
        except ValueError:
            problems["modified"].append(MANIFEST_NAME)
        else:
            problems["fields"] = _tampered_fields(recorded, manifest)
            if recorded.get("files") != manifest["files"]:
                problems["fields"].append({"field": "files",
                                           "recorded": "differs",
                                           "expected": "matches the source"})
    ok = not any(problems.values())
    return {"ok": ok, "output": str(output),
            "plugin_version": manifest["plugin_version"],
            "files": len(files), "problems": problems}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/plugin_export",
                        help="target directory (default: dist/plugin_export)")
    parser.add_argument("--layout", choices=("archive", "flat", "engineering"),
                        default="archive",
                        help="archive keeps the ZIP layout (default); flat puts the "
                             "plugin at the repository root; engineering keeps the "
                             "plugin folder plus shared/ like the existing "
                             "distribution repository")
    parser.add_argument("--check", default=None,
                        help="compare an existing export and report drift")
    parser.add_argument("--force", action="store_true",
                        help="overwrite managed files that changed or unknown files")
    parser.add_argument("--require-clean", action="store_true",
                        help="refuse a formal export unless git proves a clean commit")
    args = parser.parse_args(argv)
    try:
        if args.check:
            result = check(Path(args.check), args.layout)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        result = export(Path(args.output), args.layout, args.force,
                        args.require_clean)
    except PackagingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
