"""Build the single installable ``astrbot_plugin_digital_life`` package.

The build is content-addressed by the repository state, not by the clock: the
file list, the order, the line endings and the ZIP metadata all come from
``tools/plugin_package.json`` and ``.gitattributes``. Two runs from the same
specification therefore produce the same bytes, and any change is visible in the
manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import (REPO_ROOT, PackagingError, build_environment,
                             check_members, collect_entries, dump_json,
                             git_state, load_attribute_rules, load_spec,
                             read_entries, read_version, sha256_bytes, sha256_file,
                             write_zip)


def build(out_dir: Path, spec_path: Path | None = None,
          require_clean: bool = False) -> dict:
    spec = load_spec(spec_path) if spec_path else load_spec()
    try:
        label = out_dir.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        label = out_dir.as_posix()
    version = read_version(REPO_ROOT, spec)
    rules = load_attribute_rules(REPO_ROOT)
    entries = collect_entries(REPO_ROOT, spec)
    if not entries:
        raise PackagingError("packaging whitelist selected no files")
    payloads = read_entries(entries, rules)
    check_members([target for target, _ in payloads])

    present = {target for target, _ in payloads}
    missing = [rel for rel in spec["required"]
               if spec["top_level"] + "/" + rel not in present]
    if missing:
        raise PackagingError("required package files are missing: %s"
                             % ", ".join(sorted(missing)))

    state = git_state(REPO_ROOT)
    if require_clean:
        if state["commit"] is None:
            raise PackagingError("cannot prove a clean source: %s"
                                 % (state["reason"] or "unknown"))
        if state["dirty"]:
            raise PackagingError("working tree has uncommitted changes")

    archive_name = "%s-%s.zip" % (spec["name"], version)
    archive = out_dir / archive_name
    write_zip(archive, payloads, spec)

    manifest = {
        "schema": "digital-life-plugin-build/1",
        "build_rules_version": spec["build_rules_version"],
        "name": spec["name"],
        "plugin_version": version,
        "source_commit": state["commit"],
        "dirty": state["dirty"],
        "source_note": state["reason"],
        "archive": archive_name,
        "archive_sha256": sha256_file(archive),
        "archive_bytes": archive.stat().st_size,
        "file_count": len(payloads),
        "files": [{"path": target,
                   "sha256": sha256_bytes(data),
                   "bytes": len(data)} for target, data in payloads],
        "environment": build_environment(),
        "entrypoints": {
            "verify": "python tools/verify_unified_plugin.py --dist %s "
                      "--report %s/package_verification.json"
                      % (label, label),
            "self_test": "python tools/verify_unified_plugin.py --dist %s "
                         "--self-test --self-test-report %s/selftest.json"
                         % (label, label),
        },
        "command": "python tools/build_unified_plugin.py --out-dir %s" % label,
    }
    dump_json(out_dir / "build_manifest.json", manifest)
    (out_dir / (spec["name"] + "-" + version + ".sha256")).write_text(
        "%s  %s\n" % (manifest["archive_sha256"], archive_name),
        encoding="utf-8", newline="\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="dist",
                        help="output directory (default: dist)")
    parser.add_argument("--spec", default=None, help="packaging spec override")
    parser.add_argument("--require-clean", action="store_true",
                        help="refuse to build from a dirty or unverifiable tree")
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir = out_dir.resolve()
    try:
        manifest = build(out_dir, Path(args.spec) if args.spec else None,
                         args.require_clean)
    except PackagingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({key: manifest[key] for key in
                      ("name", "plugin_version", "archive", "archive_sha256",
                       "archive_bytes", "file_count", "source_commit", "dirty")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
