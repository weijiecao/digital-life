"""Shared packaging rules for the unified AstrBot plugin.

Every build, verification and export step reads the same specification
(``tools/plugin_package.json``) so that the file list, the version source and
the ZIP metadata cannot drift apart between entrypoints.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = Path(__file__).resolve().parent / "plugin_package.json"


class PackagingError(RuntimeError):
    """Raised when the repository cannot satisfy the packaging contract."""


# --------------------------------------------------------------------- helpers
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_spec(path: Path = SPEC_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as handle:
        spec = json.load(handle)
    if spec.get("schema") != "digital-life-plugin-package/1":
        raise PackagingError("unsupported packaging schema: %r" % spec.get("schema"))
    for key in ("name", "top_level", "plugin_dir", "version_source", "shared_packages",
                "required", "zip"):
        if key not in spec:
            raise PackagingError("packaging spec is missing %r" % key)
    return spec


def read_version(repo_root: Path, spec: dict) -> str:
    """Read the single product version and require the plugin metadata to match.

    ``VERSION`` is the only hand-edited source; ``metadata.yaml`` is derived by
    ``tools/sync_version.py`` and verified here so a build can never ship a
    stale plugin version.
    """
    from tools.project_version import (VersionError, read_metadata_version,
                                       read_version as read_product_version)

    try:
        version = read_product_version(repo_root)
    except VersionError as exc:
        raise PackagingError(str(exc)) from exc
    metadata = repo_root / spec["plugin_dir"] / "metadata.yaml"
    found = read_metadata_version(metadata)
    if found is None:
        raise PackagingError("plugin metadata missing: %s" % metadata)
    if found != version:
        raise PackagingError(
            "plugin metadata version %r does not match VERSION %r; "
            "run python tools/sync_version.py" % (found, version))
    return version


def git_state(repo_root: Path) -> dict:
    """Best-effort source identity. Absent git is reported, never invented."""
    if not (repo_root / ".git").exists():
        return {"commit": None, "dirty": None,
                "reason": "no .git directory in the working tree"}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root), check=True,
            capture_output=True, text=True).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(repo_root), check=True,
            capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"commit": None, "dirty": None,
                "reason": "git unavailable: %s" % exc}
    return {"commit": commit, "dirty": bool(status.strip()), "reason": ""}


# ------------------------------------------------------- line-ending handling
@dataclass(frozen=True)
class AttributeRule:
    pattern: str
    binary: bool
    eol: str | None


def load_attribute_rules(repo_root: Path) -> list[AttributeRule]:
    """Translate .gitattributes into the normalization this build must apply.

    The repository is the single source of truth for line endings; the ZIP must
    carry the same bytes a clean checkout would produce.
    """
    path = repo_root / ".gitattributes"
    rules: list[AttributeRule] = []
    if not path.is_file():
        return rules
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        pattern, attrs = fields[0], fields[1:]
        binary = "binary" in attrs
        eol = None
        for attr in attrs:
            if attr.startswith("eol="):
                eol = attr.split("=", 1)[1]
        if not binary and eol is None and "text" in attrs:
            eol = "lf"
        rules.append(AttributeRule(pattern, binary, eol))
    return rules


def _matches(pattern: str, relpath: str) -> bool:
    if pattern == "*":
        return True
    if "/" in pattern:
        return fnmatch.fnmatch(relpath, pattern)
    return fnmatch.fnmatch(relpath.rsplit("/", 1)[-1], pattern)


def normalize_content(data: bytes, relpath: str,
                      rules: list[AttributeRule]) -> bytes:
    """Apply the last matching attribute rule; binary files stay untouched."""
    chosen = None
    for rule in rules:
        if _matches(rule.pattern, relpath):
            chosen = rule
    if chosen is None or chosen.binary or chosen.eol is None:
        return data
    text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if chosen.eol == "crlf":
        text = text.replace(b"\n", b"\r\n")
    return text


# --------------------------------------------------------------- file selection
def _excluded(relpath: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relpath, pattern) for pattern in patterns)


def collect_entries(repo_root: Path, spec: dict) -> list[tuple[str, Path]]:
    """Return ``(path inside the archive, source file)`` in a stable order.

    Only whitelisted directories are packaged; the plugin host directory does
    not contain the shared runtime packages, so both are mapped explicitly.
    """
    patterns = spec.get("exclude", [])
    entries: list[tuple[str, Path]] = []
    seen: dict[str, Path] = {}
    sources = [(spec["plugin_dir"], spec["top_level"])]
    sources += [(item["source"], spec["top_level"] + "/" + item["target"])
                for item in spec["shared_packages"]]
    for source_dir, target_dir in sources:
        base = repo_root / source_dir
        if not base.is_dir():
            raise PackagingError("packaging source missing: %s" % source_dir)
        package = target_dir.rsplit("/", 1)[-1]
        for file in sorted(base.rglob("*"), key=lambda p: p.as_posix()):
            if not file.is_file():
                continue
            relative = file.relative_to(base).as_posix()
            candidates = (relative, package + "/" + relative,
                          target_dir + "/" + relative,
                          source_dir + "/" + relative)
            if any(_excluded(item, patterns) for item in candidates):
                continue
            target = target_dir + "/" + relative
            if target in seen:
                raise PackagingError(
                    "duplicate archive member %s (from %s and %s)"
                    % (target, seen[target], file))
            seen[target] = file
            entries.append((target, file))
    entries.sort(key=lambda item: item[0])
    return entries


def read_entries(entries: list[tuple[str, Path]],
                 rules: list[AttributeRule]) -> list[tuple[str, bytes]]:
    """Load and normalize every source file, reporting relative paths."""
    payloads: list[tuple[str, bytes]] = []
    for target, file in entries:
        data = file.read_bytes()
        relative = target.split("/", 1)[1] if "/" in target else target
        normalized = normalize_content(data, relative, rules)
        payloads.append((target, normalized))
    return payloads


# --------------------------------------------------------------------- ZIP I/O
def write_zip(path: Path, payloads: list[tuple[str, bytes]], spec: dict) -> None:
    import zipfile

    options = spec["zip"]
    date_time = tuple(options.get("date_time", (1980, 1, 1, 0, 0, 0)))
    method = (zipfile.ZIP_DEFLATED if options.get("compression") == "deflate"
              else zipfile.ZIP_STORED)
    level = int(options.get("compress_level", 6))
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", method) as archive:
        for target, data in payloads:
            info = zipfile.ZipInfo(target, date_time=date_time)
            info.compress_type = method
            info.create_system = int(options.get("create_system", 0))
            info.external_attr = int(options.get("external_attr", 0o644)) << 16
            info.flag_bits = 0x800  # UTF-8 names, independent of the host locale
            archive.writestr(info, data, compresslevel=level)


def check_members(names: list[str]) -> None:
    """Reject member names that could escape the extraction root."""
    seen: set[str] = set()
    for name in names:
        if not name or name.endswith("/"):
            raise PackagingError("directory member is not allowed: %r" % name)
        if name.startswith(("/", "\\")) or ":" in name.split("/")[0]:
            raise PackagingError("absolute member path: %r" % name)
        parts = name.replace("\\", "/").split("/")
        if any(part in ("..", ".") for part in parts):
            raise PackagingError("member path traversal: %r" % name)
        if name in seen:
            raise PackagingError("duplicate member: %r" % name)
        seen.add(name)


def build_environment() -> dict:
    """Record the interpreter and compressor used for a build."""
    return {
        "python": sys.version.split()[0],
        "implementation": sys.implementation.name,
        "zlib": zlib.ZLIB_VERSION,
        "platform": os.name,
    }


def dump_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n", encoding="utf-8", newline="\n")
