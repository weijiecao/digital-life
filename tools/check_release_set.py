"""Check that a release directory holds one complete, consistent version set.

A deliverable set for version ``V`` contains exactly::

    astrbot_plugin_digital_life-V.zip
    digital-life-source-V.zip
    digital-life-engineering-V.zip
    digital-life-release-V.json
    SHA256SUMS-V.txt

The check verifies names, versions inside the archives, hashes/sizes declared by
the release JSON, and the checksum file. Mixing files from another version makes
the whole set fail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.project_version import VersionError, read_metadata_version  # noqa: E402
from tools.project_version import read_version as read_product_version  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "astrbot_plugin_digital_life"
SCHEMA = "digital-life-release-set/1"
VERSION_TOKEN = re.compile(r"\d+\.\d+\.\d+(?:-(?:dev|rc)\.\d+)?")


def expected_names(version: str) -> set[str]:
    return {
        "%s-%s.zip" % (PLUGIN, version),
        "digital-life-source-%s.zip" % version,
        "digital-life-engineering-%s.zip" % version,
        "digital-life-release-%s.json" % version,
        "SHA256SUMS-%s.txt" % version,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _problems_of(items: list) -> list[dict]:
    return [item if isinstance(item, dict) else {"problem": str(item)}
            for item in items]


def _check_plugin_zip(path: Path, version: str, problems: list[dict]) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        problems.append({"file": path.name, "problem": "not a valid zip"})
        return
    marker = PLUGIN + "/metadata.yaml"
    if marker not in names:
        problems.append({"file": path.name, "problem": "missing %s" % marker})
        return
    text = zipfile.ZipFile(path).read(marker).decode("utf-8")
    found = None
    for line in text.splitlines():
        if line.startswith("version:"):
            found = line.split(":", 1)[1].strip()
    if found != version:
        problems.append({"file": path.name,
                         "problem": "plugin version %r != %r" % (found, version)})


def _check_source_zip(path: Path, version: str, problems: list[dict]) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "VERSION" not in names:
                problems.append({"file": path.name, "problem": "missing VERSION"})
                return
            declared = archive.read("VERSION").decode("utf-8").strip()
            metadata_member = "dev/%s/metadata.yaml" % PLUGIN
            found = None
            if metadata_member in names:
                text = archive.read(metadata_member).decode("utf-8")
                for line in text.splitlines():
                    if line.startswith("version:"):
                        found = line.split(":", 1)[1].strip()
    except zipfile.BadZipFile:
        problems.append({"file": path.name, "problem": "not a valid zip"})
        return
    if declared != version:
        problems.append({"file": path.name,
                         "problem": "VERSION %r != %r" % (declared, version)})
    if found != version:
        problems.append({"file": path.name,
                         "problem": "metadata version %r != %r" % (found, version)})


def _check_release_json(path: Path, version: str, directory: Path,
                        problems: list[dict]) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append({"file": path.name, "problem": "invalid JSON: %s" % exc})
        return
    if value.get("version") != version:
        problems.append({"file": path.name,
                         "problem": "declares version %r" % value.get("version")})
    attachments = value.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        problems.append({"file": path.name, "problem": "no attachments list"})
        return
    for item in attachments:
        name = item.get("name")
        if not name or name not in expected_names(version):
            problems.append({"file": path.name,
                             "problem": "unexpected attachment %r" % name})
            continue
        target = directory / name
        if not target.is_file():
            problems.append({"file": path.name,
                             "problem": "attachment missing: %s" % name})
            continue
        if item.get("sha256") and sha256(target) != item["sha256"]:
            problems.append({"file": name, "problem": "sha256 mismatch"})
        if item.get("bytes") and target.stat().st_size != item["bytes"]:
            problems.append({"file": name, "problem": "size mismatch"})
    engineering = "digital-life-engineering-%s.zip" % version
    if engineering in value.get("self_reference", []):
        problems.append({"file": path.name,
                         "problem": "engineering zip must not record its own hash"})


def _check_sums(path: Path, version: str, directory: Path,
                problems: list[dict]) -> None:
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            if line.strip():
                problems.append({"file": path.name, "problem": "invalid line"})
            continue
        entries[parts[1].lstrip("*")] = parts[0]
    for name in expected_names(version) - {path.name}:
        if name not in entries:
            problems.append({"file": path.name, "problem": "missing entry %s" % name})
            continue
        target = directory / name
        if not target.is_file():
            problems.append({"file": path.name, "problem": "entry for missing %s" % name})
        elif sha256(target) != entries[name]:
            problems.append({"file": path.name, "problem": "hash mismatch for %s" % name})


def check_release_set(root: Path = REPO_ROOT, release_dir: str = "dist/release",
                      version: str | None = None) -> dict:
    root = Path(root)
    directory = root / release_dir
    try:
        version = version or read_product_version(root)
    except VersionError as exc:
        return {"schema": SCHEMA, "ok": False, "version": None,
                "problems": [{"problem": str(exc)}]}
    problems: list[dict] = []
    if not directory.is_dir():
        return {"schema": SCHEMA, "ok": False, "version": version,
                "problems": [{"problem": "missing release directory: %s"
                                         % release_dir}]}
    present = {path.name for path in directory.iterdir() if path.is_file()}
    expected = expected_names(version)
    for name in sorted(expected - present):
        problems.append({"problem": "missing deliverable", "file": name})
    for name in sorted(present - expected):
        tokens = VERSION_TOKEN.findall(name)
        mixed = [token for token in tokens if token != version]
        problems.append({"problem": "file from another version" if mixed
                                   else "unexpected file", "file": name})

    plugin_zip = directory / ("%s-%s.zip" % (PLUGIN, version))
    source_zip = directory / ("digital-life-source-%s.zip" % version)
    release_json = directory / ("digital-life-release-%s.json" % version)
    sums = directory / ("SHA256SUMS-%s.txt" % version)
    if plugin_zip.is_file():
        _check_plugin_zip(plugin_zip, version, problems)
    if source_zip.is_file():
        _check_source_zip(source_zip, version, problems)
    if release_json.is_file():
        _check_release_json(release_json, version, directory, problems)
    if sums.is_file():
        _check_sums(sums, version, directory, problems)
    return {"schema": SCHEMA, "ok": not problems, "version": version,
            "directory": release_dir,
            "files": sorted(present), "problems": _problems_of(problems)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", default="dist/release")
    parser.add_argument("--version", default=None)
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)
    result = check_release_set(REPO_ROOT, args.release_dir, args.version)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                     sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")
    print(json.dumps({"ok": result["ok"], "version": result["version"],
                      "problems": result["problems"]}, ensure_ascii=False,
                     indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
