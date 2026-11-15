"""打包插件：生成可安装/可分发的 zip 与 SHA256。

产物：dist/astrbot_plugin_digital_memory_store-<version>.zip
      dist/astrbot_plugin_digital_memory_store-<version>.zip.sha256

zip 内结构：astrbot_plugin_digital_memory_store/...（可直接解压到 data/plugins/）
排除：__pycache__、*.pyc
"""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV_ROOT = HERE.parent
PLUGIN = DEV_ROOT / 'astrbot_plugin_digital_memory_store'
# 不随包分发的内部文档（保留在仓库里）
EXCLUDE_FILES = {'测试前功能清单.md', '项目节点进度.md'}
# 不随包分发的目录：版本库、虚拟环境、编辑器与缓存
EXCLUDE_DIRS = {'.git', '.github', '.venv', 'venv', '__pycache__',
                '.idea', '.vscode', '.pytest_cache', '.mypy_cache'}


def plugin_version(plugin_dir=PLUGIN):
    for line in (plugin_dir / 'metadata.yaml').read_text(encoding='utf-8').splitlines():
        if line.startswith('version:'):
            return line.split(':', 1)[1].strip()
    raise ValueError('metadata.yaml 缺 version')


def build(out_dir=None, plugin_dir=PLUGIN):
    plugin_dir = Path(plugin_dir)
    version = plugin_version(plugin_dir)
    out_dir = Path(out_dir or DEV_ROOT.parent / 'dist')
    out_dir.mkdir(parents=True, exist_ok=True)
    name = '%s-%s.zip' % (plugin_dir.name, version)
    archive = out_dir / name
    files = []
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(plugin_dir.rglob('*')):
            if path.is_dir():
                continue
            rel_parts = path.relative_to(plugin_dir).parts
            if any(part in EXCLUDE_DIRS for part in rel_parts):
                continue
            if path.suffix == '.pyc':
                continue
            if path.name in EXCLUDE_FILES:
                continue
            arcname = plugin_dir.name + '/' + path.relative_to(plugin_dir).as_posix()
            handle.write(path, arcname)
            files.append(arcname)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix('.zip.sha256')
    checksum.write_text('%s  %s\n' % (digest, name), encoding='utf-8')
    return {'archive': str(archive), 'checksum': str(checksum), 'sha256': digest,
            'version': version, 'files': len(files), 'size_mb': round(
                archive.stat().st_size / 1024 / 1024, 2)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='打包插件 zip')
    parser.add_argument('--out-dir', default='')
    args = parser.parse_args(argv)
    result = build(args.out_dir or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
