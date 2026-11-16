"""Check the built archive with AstrBot's own plugin-archive inspector.

The WebUI upload and "install from repository" paths both use
``_PluginUpdater.inspect_plugin_archive``, so running it here catches metadata
problems that a plain unzip check cannot. It needs an AstrBot source checkout or
installation::

    python tools/check_astrbot_install.py --dist dist \\
        --astrbot-root testbed/AstrBot-4.29.3 \\
        --report dist/astrbot_install_check.json

This still does not start a live instance; it validates the archive the same way
the host does before loading the plugin.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import (REPO_ROOT, PackagingError, dump_json, load_spec,
                             sha256_file)

SCHEMA = "digital-life-astrbot-install-check/1"


def check(bundle: Path, astrbot_root: Path, manifest: dict) -> dict:
    bundle = bundle.resolve()
    root = astrbot_root.resolve()
    if not (root / "astrbot").exists():
        raise PackagingError("no astrbot package under %s" % astrbot_root)
    spec = load_spec()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # AstrBot resolves data/, t2i templates and plugin paths relative to the
    # process cwd, so the import must not run inside the repository tree.
    import os
    import tempfile

    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="dl-astrbot-check-") as workspace:
        os.chdir(workspace)
        try:
            from astrbot.core.star.updater import _PluginUpdater  # noqa: PLC0415

            return _inspect(_PluginUpdater, bundle, manifest, spec, root)
        finally:
            os.chdir(previous)


def _inspect(cls, bundle: Path, manifest: dict, spec: dict, root: Path) -> dict:
    checks: list[dict] = []

    def record(ident: str, ok: bool, detail: str) -> None:
        checks.append({"id": ident, "ok": ok, "detail": detail})

    record("bundle-sha256",
           sha256_file(bundle) == manifest["archive_sha256"],
           "bundle hash matches the build manifest")

    inspection = cls.inspect_plugin_archive(str(bundle))
    entry = inspection.get("metadata_entry")
    metadata = inspection.get("metadata") or {}
    expected = spec["top_level"] + "/metadata.yaml"
    record("metadata-entry", entry == expected,
           "metadata entry %r (expected %r)" % (entry, expected))
    record("metadata-name", metadata.get("name") == spec["name"],
           "name %r (expected %r)" % (metadata.get("name"), spec["name"]))
    record("metadata-version", metadata.get("version") == manifest["plugin_version"],
           "version %r (expected %r)"
           % (metadata.get("version"), manifest["plugin_version"]))
    record("metadata-display-name", bool(metadata.get("display_name")),
           "display_name %r" % metadata.get("display_name"))
    return {
        "schema": SCHEMA,
        "ok": all(item["ok"] for item in checks),
        "bundle": bundle.name,
        "astrbot_root": str(root),
        "inspector": "_PluginUpdater.inspect_plugin_archive",
        "metadata_entry": entry,
        "metadata": {key: metadata.get(key) for key in
                     ("name", "version", "display_name", "author", "desc")},
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist",
                        help="directory holding build_manifest.json")
    parser.add_argument("--bundle", default=None, help="override the archive path")
    parser.add_argument("--astrbot-root", required=True,
                        help="directory containing the astrbot package")
    parser.add_argument("--report", default=None, help="write the JSON result here")
    args = parser.parse_args(argv)

    dist = Path(args.dist)
    manifest_path = dist / "build_manifest.json"
    if not manifest_path.is_file():
        print(json.dumps({"ok": False, "error": "no build manifest in %s" % dist},
                         ensure_ascii=False))
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle = Path(args.bundle) if args.bundle else dist / manifest["archive"]
    try:
        result = check(bundle, Path(args.astrbot_root), manifest)
    except PackagingError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # AstrBot raises its own error types
        result = {"schema": SCHEMA, "ok": False, "bundle": bundle.name,
                  "astrbot_root": str(Path(args.astrbot_root).resolve()),
                  "error": type(exc).__name__ + ": " + str(exc), "checks": []}
    if args.report:
        dump_json(Path(args.report), result)
    print(json.dumps({key: result.get(key) for key in
                      ("ok", "metadata_entry", "error")}, ensure_ascii=False,
                     indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
