"""Single source of the product version.

``VERSION`` at the repository root is the only hand-edited version. Everything
else is derived from it and verified in CI:

* the plugin ``metadata.yaml`` (written by ``tools/sync_version.py``),
* package names and build/export manifests,
* the runtime status field (the plugin reads its own metadata at import time),
* the release set (``tools/check_release_set.py``).

Commands::

    python tools/sync_version.py          # write the derived fields
    python tools/sync_version.py --check  # read-only drift check (CI)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = "VERSION"
METADATA_RELATIVE = "dev/astrbot_plugin_digital_life/metadata.yaml"

# 0.8.0-dev.1 / 0.8.0-rc.1 / 0.8.0
PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-(?:dev|rc)\.\d+)?$")


class VersionError(RuntimeError):
    """Raised when the version source is missing, empty or malformed."""


def read_version(root: Path = REPO_ROOT) -> str:
    path = Path(root) / VERSION_FILE
    if not path.is_file():
        raise VersionError("missing %s at the repository root" % VERSION_FILE)
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise VersionError("%s is empty" % VERSION_FILE)
    if not PATTERN.match(value):
        raise VersionError("%s is not a product version: %r" % (VERSION_FILE, value))
    return value


def release_level(version: str) -> str:
    """Return ``dev``, ``rc`` or ``stable`` for a valid product version."""
    if "-dev." in version:
        return "dev"
    if "-rc." in version:
        return "rc"
    return "stable"


def read_metadata_version(path: Path) -> str | None:
    """Return the ``version`` value of a plugin metadata file, if present."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def write_metadata_version(path: Path, version: str) -> bool:
    """Set ``version`` in the metadata file; return True when the file changed."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("version:"):
            replacement = "version: " + version
            if lines[index] == replacement:
                return False
            lines[index] = replacement
            path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            return True
    raise VersionError("no version field in %s" % path)


def derived_mismatches(root: Path = REPO_ROOT,
                       metadata_relative: str = METADATA_RELATIVE) -> list[dict]:
    """Return every derived field that does not match VERSION."""
    version = read_version(root)
    problems: list[dict] = []
    metadata = Path(root) / metadata_relative
    found = read_metadata_version(metadata)
    if found is None:
        problems.append({"field": metadata_relative, "problem": "metadata missing"})
    elif found != version:
        problems.append({"field": metadata_relative, "expected": version,
                         "found": found})
    return problems


def sync(root: Path = REPO_ROOT,
         metadata_relative: str = METADATA_RELATIVE) -> list[str]:
    """Write derived fields from VERSION; return the files that changed."""
    version = read_version(root)
    changed: list[str] = []
    metadata = Path(root) / metadata_relative
    if write_metadata_version(metadata, version):
        changed.append(metadata_relative)
    return changed


def check(root: Path = REPO_ROOT,
          metadata_relative: str = METADATA_RELATIVE) -> dict:
    """Read-only drift check used by CI and release builds."""
    try:
        version = read_version(root)
        problems = derived_mismatches(root, metadata_relative)
    except VersionError as exc:
        return {"ok": False, "version": None, "problems": [{"problem": str(exc)}]}
    return {"ok": not problems, "version": version,
            "level": release_level(version), "problems": problems}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify derived fields without writing")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args(argv)
    if args.check:
        result = check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    try:
        changed = sync()
        version = read_version()
    except VersionError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "version": version, "changed": changed},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
