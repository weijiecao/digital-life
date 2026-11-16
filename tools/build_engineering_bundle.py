"""Assemble the complete deliverable set for one product version.

The set is built from an already verified plugin package plus the source tree::

    python tools/build_engineering_bundle.py --dist dist --out-dir dist/release

Deliverables (version ``V``):

* ``astrbot_plugin_digital_life-V.zip``     installable plugin (copied, not rebuilt)
* ``digital-life-source-V.zip``             source tree at the built commit
* ``digital-life-engineering-V.zip``        source zip + plugin zip + notes + acceptance summary
* ``digital-life-release-V.json``           attachments, hashes, level and limits
* ``SHA256SUMS-V.txt``                      checksums of the other four files

Nothing is rebuilt here: the plugin ZIP must already exist and match its build
manifest, so the delivered package and the verified package cannot diverge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.project_version import (VersionError, read_version as read_product_version,
                                   release_level)
from tools.repo_packaging import (REPO_ROOT, PackagingError, git_state, load_spec,
                                  write_zip)

PLUGIN = "astrbot_plugin_digital_life"
SCHEMA = "digital-life-release/2"
LIMITS = [
    "development build, not an RC; do not connect to V1 production",
    "real AstrBot/NapCat instance, Windows install, browser UI and 72h soak are not verified",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_source_zip(root: Path, target: Path) -> None:
    state = git_state(root)
    if state["commit"] is None:
        raise PackagingError("cannot create the source zip without git: %s"
                             % (state["reason"] or "unknown"))
    if state["dirty"]:
        raise PackagingError("refusing to archive a dirty tree; commit first")
    target.parent.mkdir(parents=True, exist_ok=True)
    # ``git archive`` honours the text/eol attributes; without an explicit LF
    # eol it writes the Windows native CRLF, which made the released source zip
    # rebuild a different plugin. Force the canonical form for the archive.
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
         "archive", "--format=zip", "-o", str(target), "HEAD"],
        cwd=str(root), capture_output=True, text=True)
    if result.returncode != 0:
        raise PackagingError("git archive failed: %s" % result.stderr.strip())


def _acceptance_summary(dist: Path) -> str:
    lines = []
    regression = dist / "regression.json"
    if regression.is_file():
        value = json.loads(regression.read_text(encoding="utf-8"))
        lines.append("source regression: %s/%s passed, %s skipped"
                     % (value.get("passed"), value.get("total"), value.get("skipped")))
    verification = dist / "package_verification.json"
    if verification.is_file():
        value = json.loads(verification.read_text(encoding="utf-8"))
        lines.append("package verification: %s (host=%s, %d checks)"
                     % (value.get("ok"), value.get("host"),
                        len(value.get("checks", []))))
    install = dist / "astrbot_install_check.json"
    if install.is_file():
        value = json.loads(install.read_text(encoding="utf-8"))
        lines.append("AstrBot archive inspection: %s" % value.get("ok"))
    package_tests = dist / "package_regression.json"
    if package_tests.is_file():
        value = json.loads(package_tests.read_text(encoding="utf-8"))
        for suite in value.get("suites", []):
            lines.append("package suite %s: %s/%s passed"
                         % (suite.get("id"), suite.get("passed"), suite.get("total")))
    return "\n".join(lines) or "no acceptance summary available"


EVIDENCE_SCHEMA = "digital-life-engineering-evidence/1"
BOUND_REPORTS = {
    "regression.json": "tested_commit",
    "package_regression.json": "tested_commit",
    "package_verification.json": "source_commit",
    "selftest.json": "source_commit",
    "astrbot_install_check.json": "source_commit",
    "ui_dom.json": "source_commit",
}
ARTIFACT_FIELDS = ("artifact_sha256", "archive_sha256")


def _bind_evidence(dist: Path, build: dict) -> dict[str, Path]:
    """Return the reports that provably describe this build.

    Every included report must carry the same commit, be non-dirty and, when it
    records an artifact hash, the same artifact. ``release_set.json`` is never
    included: the release check runs after this bundle, so any copy in ``dist``
    belongs to an earlier set and is exactly the stale evidence this guard
    exists to stop.
    """
    commit = build.get("source_commit")
    artifact = build.get("archive_sha256")
    version = build.get("plugin_version")
    if not commit:
        raise PackagingError("cannot bind evidence without a source commit")
    included: dict[str, Path] = {}
    problems: list[str] = []
    for name, field in BOUND_REPORTS.items():
        path = dist / name
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            problems.append("%s: not valid JSON" % name)
            continue
        if value.get(field) != commit:
            problems.append("%s: %s=%r, expected %s"
                            % (name, field, value.get(field), commit[:8]))
        if value.get("dirty") is not False:
            problems.append("%s: dirty=%r" % (name, value.get("dirty")))
        if value.get("plugin_version") not in (None, version):
            problems.append("%s: version=%r" % (name, value.get("plugin_version")))
        for field_name in ARTIFACT_FIELDS:
            recorded = value.get(field_name)
            if recorded is not None and recorded != artifact:
                problems.append("%s: %s=%r" % (name, field_name, recorded[:12]))
        nested = value.get("archive")
        if isinstance(nested, dict) and nested.get("sha256") not in (None, artifact):
            problems.append("%s: archive.sha256=%r" % (name, nested["sha256"][:12]))
        included[name] = path
    if problems:
        raise PackagingError("refusing to package stale evidence: %s"
                             % "; ".join(problems))
    return included


def build(root: Path = REPO_ROOT, dist: str = "dist", out_dir: str = "dist/release",
          source_zip: str | None = None, notes: str | None = None,
          build_rules: str | None = None) -> dict:
    root = Path(root)
    dist_path = root / dist
    target = root / out_dir
    version = read_product_version(root)
    manifest_path = dist_path / "build_manifest.json"
    if not manifest_path.is_file():
        raise PackagingError("no build manifest in %s" % dist)
    build = json.loads(manifest_path.read_text(encoding="utf-8"))
    if build.get("plugin_version") != version:
        raise PackagingError("build manifest version %r != VERSION %r"
                             % (build.get("plugin_version"), version))
    plugin_zip = dist_path / build["archive"]
    if not plugin_zip.is_file():
        raise PackagingError("plugin package missing: %s" % plugin_zip)
    if sha256(plugin_zip) != build["archive_sha256"]:
        raise PackagingError("plugin package does not match its manifest hash")

    target.mkdir(parents=True, exist_ok=True)
    existing = [path.name for path in target.iterdir()
                if path.is_file() and version not in path.name]
    if existing:
        raise PackagingError("release directory already holds other versions: %s"
                             % existing)

    plugin_name = "%s-%s.zip" % (PLUGIN, version)
    plugin_target = target / plugin_name
    shutil.copy2(plugin_zip, plugin_target)

    source_name = "digital-life-source-%s.zip" % version
    source_target = target / source_name
    if source_zip:
        shutil.copy2(Path(source_zip), source_target)
    else:
        _git_source_zip(root, source_target)

    notes_text = ""
    if notes and Path(notes).is_file():
        notes_text = Path(notes).read_text(encoding="utf-8")
    summary = _acceptance_summary(dist_path)

    engineering_name = "digital-life-engineering-%s.zip" % version
    engineering_target = target / engineering_name
    spec = load_spec()
    with tempfile.TemporaryDirectory(prefix="dl-engineering-") as temp:
        staging = Path(temp)
        for name in (plugin_name, source_name):
            shutil.copy2(target / name, staging / name)
        (staging / "RELEASE_NOTES.md").write_text(notes_text or
                                                 "see the repository changelog\n",
                                                 encoding="utf-8", newline="\n")
        (staging / "ACCEPTANCE.md").write_text(
            "# Acceptance summary\n\nversion: %s\nsource_commit: %s\ndirty: %s\n\n%s\n\n"
            "release-set check: produced after this bundle (see dist/release_set.json "
            "and the release job log); only reports bound to this commit and "
            "artifact are included in EVIDENCE/.\n"
            % (version, build.get("source_commit"), build.get("dirty"), summary),
            encoding="utf-8", newline="\n")
        # The reports are part of the deliverable, but only the ones that
        # provably describe *this* build. A stale report is an error, not
        # something to package quietly.
        evidence = staging / "EVIDENCE"
        evidence.mkdir()
        bound = _bind_evidence(dist_path, build)
        for name, source in bound.items():
            shutil.copy2(source, evidence / name)
        export_manifest = dist_path / "plugin_export" / "SOURCE_MANIFEST.json"
        if export_manifest.is_file():
            shutil.copy2(export_manifest, evidence / "export_SOURCE_MANIFEST.json")
        (evidence / "INDEX.json").write_text(
            json.dumps({"schema": EVIDENCE_SCHEMA,
                        "version": version,
                        "source_commit": build.get("source_commit"),
                        "artifact_sha256": build.get("archive_sha256"),
                        "reports": [{"name": name,
                                     "bytes": (evidence / name).stat().st_size,
                                     "sha256": sha256(evidence / name)}
                                    for name in sorted(bound)]},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        inner = [(path.relative_to(staging).as_posix(), path.read_bytes())
                 for path in sorted(staging.rglob("*")) if path.is_file()]
        checksums = "\n".join("%s  %s" % (hashlib.sha256(data).hexdigest(), name)
                              for name, data in inner) + "\n"
        (staging / "INNER_SHA256SUMS.txt").write_text(checksums, encoding="utf-8",
                                                      newline="\n")
        inner.append(("INNER_SHA256SUMS.txt", checksums.encode("utf-8")))
        write_zip(engineering_target, sorted(inner), spec)

    release = {
        "schema": SCHEMA,
        "name": PLUGIN,
        "version": version,
        "level": release_level(version),
        "source_commit": build.get("source_commit"),
        "dirty": build.get("dirty"),
        "build_rules_version": build.get("build_rules_version"),
        "plugin_version_from": "VERSION",
        "attachments": [
            {"name": plugin_name, "kind": "plugin", "bytes": plugin_target.stat().st_size,
             "sha256": sha256(plugin_target)},
            {"name": source_name, "kind": "source", "bytes": source_target.stat().st_size,
             "sha256": sha256(source_target)},
            {"name": engineering_name, "kind": "engineering",
             "bytes": engineering_target.stat().st_size,
             "sha256": sha256(engineering_target)},
        ],
        "links": {"source": "https://github.com/weijiecao/digital-life",
                  "downloads": "https://github.com/weijiecao/astrbot_plugin_digital_life/releases"},
        "limits": LIMITS,
    }
    release_name = "digital-life-release-%s.json" % version
    release_path = target / release_name
    release_path.write_text(json.dumps(release, ensure_ascii=False, indent=2,
                                       sort_keys=True) + "\n",
                            encoding="utf-8", newline="\n")

    sums_name = "SHA256SUMS-%s.txt" % version
    sums = "\n".join("%s  %s" % (sha256(target / item["name"]), item["name"])
                     for item in release["attachments"])
    sums += "\n%s  %s\n" % (sha256(release_path), release_name)
    (target / sums_name).write_text(sums, encoding="utf-8", newline="\n")

    from tools.check_release_set import check_release_set
    result = check_release_set(root, out_dir, version)
    if not result["ok"]:
        raise PackagingError("release set is inconsistent: %s" % result["problems"])
    return {"version": version, "output": out_dir,
            "files": sorted(path.name for path in target.iterdir() if path.is_file()),
            "release_set": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--out-dir", default="dist/release")
    parser.add_argument("--source-zip", default=None,
                        help="pre-built source zip (otherwise git archive HEAD)")
    parser.add_argument("--notes", default=None, help="release notes to embed")
    args = parser.parse_args(argv)
    try:
        result = build(REPO_ROOT, args.dist, args.out_dir, args.source_zip,
                       args.notes)
    except (PackagingError, VersionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "version": result["version"],
                      "files": result["files"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
