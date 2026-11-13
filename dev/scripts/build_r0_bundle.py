"""Build a deterministic, source-bound RC2/RC3 bundle. Packaging does not grant release.

The bundle must carry every directory the runtime entry points import, plus the
console static assets. ``build`` therefore verifies the archive in an isolated
subprocess before writing the manifest: a missing package or view file fails the
build instead of shipping an incomplete "current" package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DEV = Path(__file__).resolve().parents[1]
OUT = DEV.parent / '_publish' / 'digital-life' / 'releases' / 'memory-engine-rc3-queryguard.zip'

# 新增主链路目录必须整体进入发布包；此前只收集三个顶层目录，导致隔离安装
# 无法导入 memory_engine.native_host（缺少 astrbot_memory_input）。
PACKAGE_DIRS = ('v1_5_consolidator', 'memory_engine', 'astrbot_memory_input',
                'memory_output', 'dlm_console', 'host_shadow')
EXTRA_FILES = ('requirements-release.txt', 'RELEASE_CANDIDATE.md', 'README.md')
# 递归收集的静态资源与控制台视图；缺一个控制台页面就打不开。
STATIC_SUFFIXES = frozenset({'.py', '.yaml', '.yml', '.md', '.toml',
                             '.html', '.js', '.css', '.svg'})
EXCLUDED_PARTS = frozenset({'__pycache__', '.git', '.dlm_console', 'node_modules',
                            'build', 'dist'})
EXCLUDED_SUFFIXES = frozenset({'.pyc', '.pyo'})

_IMPORT_CHECK = r'''
import pathlib, sys, zipfile
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import astrbot_memory_input, dlm_console, memory_output
from dlm_console.core import Console
from memory_engine.native_host import NativeInputService
from memory_engine.output import MemoryOutputService
from memory_output import OutputRequest
for module in (astrbot_memory_input, dlm_console, memory_output):
    path = pathlib.Path(module.__file__).resolve()
    assert path.is_relative_to(root), path
assets = ('dlm_console/web/index.html', 'dlm_console/web/app.js',
          'dlm_console/web/style.css', 'dlm_console/web/favicon.svg',
          'host_shadow/views/console/index.html', 'host_shadow/views/console/app.js',
          'host_shadow/views/console/style.css',
          'host_shadow/views/console/favicon.svg')
for name in assets:
    assert (root / name).is_file(), name
print('ok')
'''


def _included(relative: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if any(part.endswith('.egg-info') for part in relative.parts):
        return False
    suffix = relative.suffix.lower()
    return suffix in STATIC_SUFFIXES and suffix not in EXCLUDED_SUFFIXES


def source_files() -> dict[str, bytes]:
    files = {}
    for directory in PACKAGE_DIRS:
        root = DEV / directory
        if not root.is_dir():
            raise FileNotFoundError('missing package directory: %s' % directory)
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(DEV)
            if path.is_file() and _included(relative):
                files[relative.as_posix()] = path.read_bytes()
    for rel in EXTRA_FILES:
        path = DEV / rel
        if path.is_file():
            files[rel] = path.read_bytes()
    files['verify_r0_bundle.py'] = (DEV / 'scripts/verify_r0_bundle.py').read_bytes()
    return files


def verify_bundle(output: Path) -> None:
    """隔离解压后验证导入与静态资源；失败时构建不应产出“当前包”。"""
    with tempfile.TemporaryDirectory(prefix='bundle-check-') as directory:
        with zipfile.ZipFile(output) as archive:
            archive.extractall(directory)
        proc = subprocess.run([sys.executable, '-I', '-c', _IMPORT_CHECK, directory],
                              cwd=directory, capture_output=True, text=True,
                              timeout=120)
        if proc.returncode != 0:
            raise RuntimeError('bundle self-check failed: '
                               + (proc.stderr.strip() or proc.stdout.strip()))


def build(output: Path) -> dict:
    files = source_files()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    source_digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    try:
        revision = subprocess.check_output(
            ['git', '-C', str(DEV), 'rev-parse', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        revision = 'unavailable'
    inner = {'schema': 2, 'base_commit': revision, 'source_sha256': source_digest,
             'file_sha256': hashes, 'status': 'RC3-Q1; R0/R1 NO-GO; isolated evaluation only'}
    files['BUNDLE_MANIFEST.json'] = (json.dumps(inner, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    verify_bundle(output)
    manifest = {**inner, 'artifact': output.name, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'bytes': output.stat().st_size, 'files': len(files),
                'file_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    output.with_suffix('.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.out.resolve()), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
