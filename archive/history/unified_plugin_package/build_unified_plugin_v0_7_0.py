"""Build the single installable astrbot_plugin_digital_life package."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
SRC = ROOT / "astrbot_plugin_digital_life"
SHARED = ROOT / "shared"
SHARED_PACKAGES = ("astrbot_memory_input", "memory_engine", "memory_output",
                   "v1_5_consolidator", "dlm_console")
EXCLUDE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".git",
                                 ".github", ".venv", "build", "dist",
                                 "*.egg-info", ".pytest_cache", ".mypy_cache")


def version() -> str:
    for line in (SRC / "metadata.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    raise ValueError("metadata.yaml is missing version")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build() -> dict:
    ver = version()
    DIST.mkdir(parents=True, exist_ok=True)
    archive = DIST / ("astrbot_plugin_digital_life-%s.zip" % ver)
    with tempfile.TemporaryDirectory(prefix="dl-unified-build-") as temp:
        package = Path(temp) / "astrbot_plugin_digital_life"
        shutil.copytree(SRC, package, ignore=EXCLUDE)
        for name in SHARED_PACKAGES:
            source = SHARED / name
            if not source.is_dir():
                raise FileNotFoundError("missing shared package: " + name)
            shutil.copytree(source, package / name, ignore=EXCLUDE)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
            for path in sorted(package.rglob("*"), key=lambda item: item.relative_to(package.parent).as_posix()):
                if not path.is_file():
                    continue
                rel = path.relative_to(package.parent).as_posix()
                info = zipfile.ZipInfo(rel, date_time=(2026, 11, 13, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                handle.writestr(info, path.read_bytes())
    # Isolated import check for every bundled package and console asset.
    with tempfile.TemporaryDirectory(prefix="dl-unified-check-") as temp:
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(temp)
        package = Path(temp) / "astrbot_plugin_digital_life"
        sys.path.insert(0, str(package))
        try:
            import astrbot_memory_input  # noqa: F401
            import dlm_console  # noqa: F401
            import memory_engine  # noqa: F401
            import memory_output  # noqa: F401
            import v1_5_consolidator  # noqa: F401
        finally:
            sys.path.pop(0)
        for rel in ("main.py", "config.py", "_conf_schema.json", "metadata.yaml",
                    "console_api.py", "views/console/index.html"):
            if not (package / rel).is_file():
                raise AssertionError("missing package file: " + rel)
    result = {"name": "astrbot_plugin_digital_life", "version": ver,
              "archive": archive.name, "sha256": sha256(archive),
              "files": len(zipfile.ZipFile(archive).namelist()),
              "bytes": archive.stat().st_size, "tag": "v" + ver}
    (DIST / "RELEASE_UNIFIED.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DIST / ("astrbot_plugin_digital_life-%s.sha256" % ver)).write_text(
        "%s  %s\n" % (result["sha256"], archive.name), encoding="utf-8")
    return result


if __name__ == "__main__":
    DIST.mkdir(parents=True, exist_ok=True)
    print(json.dumps(build(), ensure_ascii=False, indent=2))
