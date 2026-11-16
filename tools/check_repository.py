"""Check that the repository's declared entrypoints and links are real.

Run from the repository root::

    python tools/check_repository.py            # human summary
    python tools/check_repository.py --json     # machine-readable

The command exists so that a clean clone can prove the documented build, test,
verification and export entrypoints exist, that active code never imports the
archive, and that the documents do not point at removed files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.repo_packaging import REPO_ROOT, PackagingError, load_spec, read_version

MANIFEST = Path(__file__).resolve().parent / "repository_manifest.json"

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
PY_COMMAND = re.compile(r"python(?:\.exe)?\s+((?:[\w.\\/-]+/)?[\w.-]+\.py)\b")
MODULE_COMMAND = re.compile(r"python(?:\.exe)?\s+-m\s+([\w.]+)")
ARCHIVE_IMPORT = re.compile(
    r"(?:^\s*(?:from|import)\s+archive\b)"      # module import
    r"|(?:sys\.path\.(?:insert|append)\s*\([^)]*archive)"  # path insertion
    r"|(?P<literal>['\"][^'\"]*\barchive[/\\][^'\"]*['\"])")  # path literal


def _walk_python(roots: list[str], excluded: list[str]) -> list[Path]:
    files: list[Path] = []
    for name in roots:
        base = REPO_ROOT / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if any(relative.startswith(item.rstrip("/") + "/") or relative == item
                   for item in excluded):
                continue
            files.append(path)
    return sorted(files)


def check_required_paths(manifest: dict, checks: list[dict]) -> None:
    missing = [rel for rel in manifest["required_paths"]
               if not (REPO_ROOT / rel).exists()]
    checks.append({"id": "required-paths", "ok": not missing,
                   "detail": "missing: %s" % missing if missing else
                   "%d declared paths exist" % len(manifest["required_paths"])})


def check_forbidden_paths(manifest: dict, checks: list[dict]) -> None:
    present = [rel for rel in manifest["forbidden_active_dirs"]
               if (REPO_ROOT / rel).exists()]
    checks.append({"id": "no-legacy-paths", "ok": not present,
                   "detail": "legacy paths still in the active tree: %s" % present
                   if present else "archived code is not reachable from dev/"})


def check_archive_isolation(manifest: dict, checks: list[dict]) -> None:
    allowed = set(manifest.get("archive_reference_allow", []))
    hits = []
    for path in _walk_python(manifest["active_code_roots"],
                             manifest["excluded_code_dirs"]):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in allowed:
            continue
        for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            match = ARCHIVE_IMPORT.search(line)
            if match and not line.lstrip().startswith("#"):
                hits.append("%s:%d: %s" % (relative, number, line.strip()))
    checks.append({"id": "archive-isolation", "ok": not hits,
                   "detail": "active code references archive: %s" % hits[:8] if hits
                   else "no active module imports or paths into archive/"})


def check_test_split(checks: list[dict]) -> None:
    active = {path.name for path in (REPO_ROOT / "dev" / "tests").glob("test_*.py")}
    archived = {path.name for path in (REPO_ROOT / "archive" / "tests").glob("test_*.py")}
    both = sorted(active & archived)
    checks.append({"id": "test-split", "ok": not both,
                   "detail": "tests present in both sets: %s" % both if both else
                   "active and archived test sets are disjoint (%d / %d)"
                   % (len(active), len(archived))})


def check_version(manifest: dict, checks: list[dict]) -> None:
    try:
        spec = load_spec(REPO_ROOT / manifest["package_spec"])
        version = read_version(REPO_ROOT, spec)
    except (PackagingError, FileNotFoundError) as exc:
        checks.append({"id": "version-source", "ok": False, "detail": str(exc)})
        return
    problems = []
    release_dir = REPO_ROOT / manifest["release_dir"]
    archive = release_dir / ("%s-%s.zip" % (spec["name"], version))
    if not archive.is_file():
        problems.append("no release archive for the current version: %s"
                        % archive.relative_to(REPO_ROOT).as_posix())
    for meta in release_dir.glob("*.json"):
        try:
            value = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            problems.append("invalid release manifest: %s" % meta.name)
            continue
        if value.get("name") == spec["name"] and value.get("version") != version:
            problems.append("%s declares version %r" % (meta.name, value.get("version")))
    checks.append({"id": "version-source", "ok": not problems,
                   "detail": "problems: %s" % problems if problems else
                   "metadata.yaml version %s is the only version source" % version})


def _local_target(doc: Path, target: str) -> Path | None:
    target = target.strip()
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target or target.startswith("<"):
        return None
    target = target.replace("\\", "/")
    if target.startswith("/"):
        return None  # site-absolute links are not repository paths
    candidate = (doc.parent / target).resolve()
    if candidate.exists():
        return candidate
    root_candidate = (REPO_ROOT / target).resolve()
    return root_candidate if root_candidate.exists() else candidate


def check_doc_links(manifest: dict, checks: list[dict]) -> None:
    broken = []
    for rel in manifest["active_docs"]:
        doc = REPO_ROOT / rel
        if not doc.is_file():
            broken.append("%s: missing document" % rel)
            continue
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            resolved = _local_target(doc, target)
            if resolved is None:
                continue
            if not resolved.exists():
                broken.append("%s -> %s" % (rel, target))
    checks.append({"id": "doc-links", "ok": not broken,
                   "detail": "broken links: %s" % broken[:10] if broken else
                   "%d documents link only to existing paths"
                   % len(manifest["active_docs"])})


def _module_exists(module: str) -> bool:
    parts = module.split(".")
    for base in (REPO_ROOT, REPO_ROOT / "dev"):
        for suffix in (".py", "/__init__.py"):
            candidate = base / ("/".join(parts) + suffix)
            if candidate.is_file():
                return True
        if (base / "/".join(parts)).is_dir():
            return True
    return False


def check_doc_commands(manifest: dict, checks: list[dict]) -> None:
    broken = []
    for rel in manifest["active_docs"]:
        doc = REPO_ROOT / rel
        if not doc.is_file():
            continue
        for number, line in enumerate(
                doc.read_text(encoding="utf-8").splitlines(), 1):
            for command in PY_COMMAND.findall(line):
                target = command.replace("\\", "/")
                if target.startswith("archive/"):
                    continue  # historical commands may name archived tools
                candidates = [REPO_ROOT / target, doc.parent / target,
                              REPO_ROOT / "dev" / target]
                if not any(item.is_file() for item in candidates):
                    broken.append("%s:%d: %s" % (rel, number, command))
            for module in MODULE_COMMAND.findall(line):
                if module.startswith(("unittest", "pip", "compileall", "http")):
                    continue
                if not _module_exists(module):
                    broken.append("%s:%d: -m %s" % (rel, number, module))
    checks.append({"id": "doc-commands", "ok": not broken,
                   "detail": "commands without a target: %s" % broken[:10] if broken
                   else "documented python entrypoints exist"})


def check_workflow(manifest: dict, checks: list[dict]) -> None:
    path = REPO_ROOT / manifest["workflow"]
    if not path.is_file():
        checks.append({"id": "workflow", "ok": False,
                       "detail": "workflow missing: %s" % manifest["workflow"]})
        return
    text = path.read_text(encoding="utf-8")
    problems = []
    for command in PY_COMMAND.findall(text):
        target = command.replace("\\", "/")
        if not (REPO_ROOT / target).is_file():
            problems.append("missing script: %s" % target)
    for name in ("tools/check_repository.py", "tools/build_unified_plugin.py",
                 "tools/verify_unified_plugin.py"):
        if name not in text:
            problems.append("workflow does not run %s" % name)
    for stale in ("build_r0_bundle", "verify_r0_bundle", "dev/scripts/"):
        if stale in text:
            problems.append("workflow still references %s" % stale)
    checks.append({"id": "workflow", "ok": not problems,
                   "detail": "problems: %s" % problems if problems else
                   "CI runs only existing entrypoints"})


def check_package_spec(manifest: dict, checks: list[dict]) -> None:
    try:
        spec = load_spec(REPO_ROOT / manifest["package_spec"])
    except (PackagingError, FileNotFoundError) as exc:
        checks.append({"id": "package-spec", "ok": False, "detail": str(exc)})
        return
    problems = []
    for item in [{"source": spec["plugin_dir"]}] + spec["shared_packages"]:
        if not (REPO_ROOT / item["source"]).is_dir():
            problems.append("missing source: %s" % item["source"])
    packages = {item["target"]: item["source"] for item in spec["shared_packages"]}
    for rel in spec["required"]:
        head, _, rest = rel.partition("/")
        if head in packages:
            source = REPO_ROOT / packages[head] / rest
        else:
            source = REPO_ROOT / spec["plugin_dir"] / rel
        if not source.is_file():
            problems.append("required file not found in any source: %s" % rel)
    checks.append({"id": "package-spec", "ok": not problems,
                   "detail": "problems: %s" % problems if problems else
                   "packaging spec maps %d sources" % (1 + len(spec["shared_packages"]))})


def check_ignored_paths(manifest: dict, checks: list[dict]) -> None:
    path = REPO_ROOT / ".gitignore"
    text = path.read_text(encoding="utf-8")
    missing = [item for item in manifest["ignored_paths"]
               if item.rstrip("/") not in text]
    checks.append({"id": "ignored-paths", "ok": not missing,
                   "detail": "not ignored: %s" % missing if missing else
                   "dist/, .local/ and generated testbed trees stay untracked"})


def check_archive_releases(checks: list[dict]) -> None:
    """Archived packages keep their recorded bytes; nothing silently changes."""
    import hashlib
    import zipfile

    release_dir = REPO_ROOT / "archive" / "releases"
    problems = []
    verified = 0
    for manifest_path in sorted(release_dir.glob("*.json")):
        if manifest_path.name.endswith(".verify.json"):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError:
            problems.append("%s: invalid JSON" % manifest_path.name)
            continue
        artifact = manifest.get("artifact")
        if not artifact:
            continue
        bundle = release_dir / artifact
        if not bundle.is_file():
            problems.append("%s: missing %s" % (manifest_path.name, artifact))
            continue
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if manifest.get("sha256") and digest != manifest["sha256"]:
            problems.append("%s: sha256 mismatch" % artifact)
        if manifest.get("bytes") and bundle.stat().st_size != manifest["bytes"]:
            problems.append("%s: size mismatch" % artifact)
        expected = manifest.get("file_sha256") or {}
        if expected:
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                roots = {item.split("/", 1)[0] for item in names if "/" in item}
                prefix = (next(iter(roots)) + "/") if len(roots) == 1 else ""
                for name, sha in expected.items():
                    # Manifests may store paths relative to the plugin folder
                    # while the archive keeps the astrbot_plugin_* prefix.
                    if name in names:
                        member = name
                    elif prefix and (prefix + name) in names:
                        member = prefix + name
                    else:
                        problems.append("%s: %s missing in archive" % (artifact, name))
                        continue
                    if hashlib.sha256(archive.read(member)).hexdigest() != sha:
                        problems.append("%s: %s content changed" % (artifact, name))
        verified += 1
    checks.append({"id": "archive-releases", "ok": not problems,
                   "detail": "problems: %s" % problems[:8] if problems else
                   "%d archived packages match their manifests" % verified})


def run_checks() -> list[dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checks: list[dict] = []
    check_required_paths(manifest, checks)
    check_forbidden_paths(manifest, checks)
    check_archive_isolation(manifest, checks)
    check_test_split(checks)
    check_version(manifest, checks)
    check_doc_links(manifest, checks)
    check_doc_commands(manifest, checks)
    check_workflow(manifest, checks)
    check_package_spec(manifest, checks)
    check_ignored_paths(manifest, checks)
    check_archive_releases(checks)
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args(argv)
    checks = run_checks()
    failed = [item for item in checks if not item["ok"]]
    if args.json:
        print(json.dumps({"schema": "digital-life-repository-check/1",
                          "ok": not failed, "checks": checks},
                         ensure_ascii=False, indent=2))
    else:
        for item in checks:
            print("%-18s %s  %s" % (item["id"], "ok  " if item["ok"] else "FAIL",
                                    item["detail"]))
        print("\n%s: %d checks, %d failed"
              % ("PASS" if not failed else "FAIL", len(checks), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
