"""Build the AstrBot plugin ZIP (top-level folder + metadata)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "astrbot_plugin_digital_memory_store"
RELEASES = ROOT.parent / "releases"
EXCLUDE_DIRS = {".git", ".github", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "data", "dist", "build", "backups"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".zip"}
EXCLUDE_FILES = {"测试前功能清单.md", "项目节点进度.md", ".gitignore", ".gitattributes"}


def version() -> str:
    text = (PLUGIN / "metadata.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*([^\s#]+)", text, re.M)
    if not match:
        raise SystemExit("metadata.yaml has no version")
    return match.group(1)


def files():
    for path in sorted(PLUGIN.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PLUGIN)
        if set(rel.parts) & EXCLUDE_DIRS or rel.name in EXCLUDE_FILES:
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        yield rel.as_posix(), path.read_bytes()


def build(output: Path) -> dict:
    payload = dict(files())
    required = {"main.py", "metadata.yaml", "requirements.txt",
                "_conf_schema.json", "install.ps1", "README.md", "LICENSE"}
    missing = sorted(required - set(payload))
    if missing:
        raise SystemExit(f"plugin missing required files: {missing}")
    top = PLUGIN.name
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(f"{top}/{name}", date_time=(2026, 11, 12, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest = {
        "artifact": output.name,
        "plugin": top,
        "version": version(),
        "sha256": sha,
        "bytes": output.stat().st_size,
        "files": len(payload),
        "file_sha256": {name: hashlib.sha256(data).hexdigest()
                        for name, data in sorted(payload.items())},
    }
    output.with_suffix(".json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    output.with_suffix(".sha256").write_text(sha + "  " + output.name + "\n",
                                             encoding="utf-8")
    return manifest


def validate(archive_path: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="plugin-check-") as directory:
        root = Path(directory)
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            assert all(name.startswith(PLUGIN.name + "/") for name in names)
            assert not any("__pycache__" in name or "/.git/" in name for name in names)
            archive.extractall(root)
        plugin = root / PLUGIN.name
        import yaml
        meta = yaml.safe_load((plugin / "metadata.yaml").read_text(encoding="utf-8"))
        assert meta["name"] == PLUGIN.name
        compiled = []
        for path in plugin.rglob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            compiled.append(path.relative_to(plugin).as_posix())
        return {"top_level": PLUGIN.name, "metadata_name": meta["name"],
                "version": meta["version"], "python_files_compiled": len(compiled),
                "files": len(names)}


def main() -> int:
    out = RELEASES / f"astrbot_plugin_digital_memory_store-{version()}.zip"
    manifest = build(out)
    checked = validate(out)
    result = {**manifest, "validation": checked}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
