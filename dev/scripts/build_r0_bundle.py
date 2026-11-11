"""Build a deterministic, source-bound RC2. Packaging does not grant release."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

DEV = Path(__file__).resolve().parents[1]
OUT = DEV.parent / '_publish' / 'digital-life' / 'releases' / 'memory-engine-r0-rc3.zip'


def source_files() -> dict[str, bytes]:
    files = {}
    for directory in ('v1_5_consolidator', 'memory_engine', 'host_shadow'):
        for path in sorted((DEV / directory).glob('*')):
            if path.is_file() and path.suffix in {'.py', '.yaml', '.md'}:
                files[path.relative_to(DEV).as_posix()] = path.read_bytes()
    for rel in ('requirements-release.txt', 'RELEASE_CANDIDATE.md'):
        files[rel] = (DEV / rel).read_bytes()
    files['verify_r0_bundle.py'] = (DEV / 'scripts/verify_r0_bundle.py').read_bytes()
    return files


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
             'file_sha256': hashes, 'status': 'RC3; R0/R1 NO-GO; isolated evaluation only'}
    files['BUNDLE_MANIFEST.json'] = (json.dumps(inner, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
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
