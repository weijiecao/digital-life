"""Verify every byte and run the extracted package in an isolated subprocess."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SMOKE = r'''
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from memory_engine.engine import MemoryEngine
from memory_engine.token_budget import TokenCounter
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store
import memory_engine.engine as module
assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
def source(seq, text):
    return SourceEnvelope('s', seq, hashlib.sha256(text.encode()).hexdigest(), 't', text)
store = V15Store(root / 'smoke.sqlite3')
engine = MemoryEngine(store)
original = source(1, '周五去木桥市集。')
event = engine.ingest('s', original).event_id
assert engine.ingest('s', original).event_id == event
assert not engine.ingest('s', source(1, '改写旧输入')).receipt_ok
assert engine.retrieve('s', '木桥市集')
new_event = engine.revise_source('s', 1, source(2, '周六去木桥市集，周五的计划取消。'))
assert not engine.retrieve('s', '周五去')
assert engine.history('s', event)
assert store.backup_to(root / 'backup.sqlite3') == 'ok'
store.close()
store = V15Store(root / 'smoke.sqlite3')
engine = MemoryEngine(store)
assert engine.retrieve('s', '木桥市集')[0]['event_id'] == new_event
counter = TokenCounter() if sys.argv[2] == '1' else None
if counter:
    packet = engine.build_rendered_context('s', '木桥市集', 200, counter)
    assert packet['items'] and counter.count(packet['rendered']) <= 200
    assert engine.build_rendered_context('s', '木桥市集', 0, counter)['rendered'] == ''
engine.withdraw_event('s', new_event)
assert not engine.retrieve('s', '木桥市集')
assert store.integrity_check() == 'ok'
store.close()
restored = V15Store(root / 'backup.sqlite3')
engine = MemoryEngine(restored)
assert engine.retrieve('s', '木桥市集')[0]['event_id'] == new_event
assert engine.history('s', event)
assert engine.ingest('s', source(3, '恢复后补充了一张车票。')).receipt_ok
assert engine.retrieve('s', '车票')
assert restored.integrity_check() == 'ok'
restored.close()
print(json.dumps({'isolated_import': True, 'ingest_repeat_conflict': True,
                  'revision_history_restart': True, 'withdraw': True,
                  'backup_restore_resume': True,
                  'exact_budget': bool(counter), 'integrity': True}))
'''


def locate(bundle):
    if bundle:
        return Path(bundle).resolve()
    for name in ('memory-engine-r0-rc2.zip', 'memory-engine-r0-rc1.zip'):
        for folder in (ROOT.parent / 'releases', ROOT.parent / '_publish/digital-life/releases'):
            if (folder / name).is_file():
                return folder / name
    raise FileNotFoundError('Pass --bundle /path/to/release.zip')


def verify(path: Path, exact_budget=False) -> dict:
    expected = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected['sha256'], 'archive SHA mismatch'
    assert path.stat().st_size == expected['bytes'], 'archive length mismatch'
    with tempfile.TemporaryDirectory(prefix='r0-isolated-') as directory:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names)) == expected['files'], 'duplicate/count mismatch'
            assert set(names) == set(expected['file_sha256']), 'file list mismatch'
            for name in names:
                rel = PurePosixPath(name)
                assert not rel.is_absolute() and '..' not in rel.parts and '\\' not in name and ':' not in name, 'unsafe member'
                assert hashlib.sha256(archive.read(name)).hexdigest() == expected['file_sha256'][name], name
            if 'BUNDLE_MANIFEST.json' in names:
                inner = json.loads(archive.read('BUNDLE_MANIFEST.json'))
                hashes = {name: digest for name, digest in expected['file_sha256'].items() if name != 'BUNDLE_MANIFEST.json'}
                assert inner['file_sha256'] == hashes
                digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                assert digest == inner['source_sha256'] == expected['source_sha256']
            archive.extractall(directory)
        proc = subprocess.run([sys.executable, '-I', '-c', SMOKE, directory, str(int(exact_budget))],
                              cwd=directory, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        smoke = json.loads(proc.stdout)
        cli = subprocess.run([sys.executable, '-I', '-c',
                              'import sys;sys.path.insert(0,sys.argv.pop(1));from memory_engine.cli import main;raise SystemExit(main())',
                              directory, '--db', str(Path(directory) / 'cli.sqlite3'), 'health'],
                             cwd=directory, capture_output=True, text=True, timeout=30)
        assert cli.returncode == 0, cli.stderr
    return {'verdict': 'PACKAGE_VERIFIED_NOT_RELEASE_GO', 'sha256': expected['sha256'],
            'source_sha256': expected.get('source_sha256'), 'files': expected['files'],
            'smoke': smoke, 'cli': True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', default='')
    parser.add_argument('--exact-budget', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(locate(args.bundle), args.exact_budget)
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        result = {'verdict': 'FAIL', 'error': str(exc)}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + '\n', encoding='utf-8')
    print(text)
    return int(result['verdict'] == 'FAIL')


if __name__ == '__main__':
    raise SystemExit(main())
