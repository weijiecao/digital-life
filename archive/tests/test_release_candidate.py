import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from memory_engine.cli import main
from scripts.build_r0_bundle import build
from scripts.verify_r0_bundle import verify


class TestReleaseCandidate(unittest.TestCase):
    def test_cli_default_hash_detects_rewritten_source(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ['--db', str(Path(directory) / 'test.sqlite3'), 'ingest',
                    '--scope', 's', '--seq', '1', '--text']
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args + ['原输入']), 0)
                self.assertEqual(main(args + ['原输入']), 0)
                self.assertEqual(main(args + ['改写输入']), 1)

    def test_cli_revision_hash_rejects_same_identity_different_text(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ['--db', str(Path(directory) / 'test.sqlite3')]
            with contextlib.redirect_stdout(io.StringIO()):
                main(base + ['ingest', '--scope', 's', '--seq', '1', '--text', '原计划'])
                args = base + ['revise', '--scope', 's', '--old-seq', '1', '--new-seq', '2', '--text']
                self.assertEqual(main(args + ['新计划']), 0)
                self.assertEqual(main(args + ['新计划']), 0)
                with self.assertRaises(ValueError):
                    main(args + ['另外计划'])

    def test_deterministic_package_is_independently_runnable(self):
        with tempfile.TemporaryDirectory() as directory:
            left, right = Path(directory) / 'a.zip', Path(directory) / 'b.zip'
            a, b = build(left), build(right)
            self.assertEqual(a['sha256'], b['sha256'])
            self.assertEqual(left.read_bytes(), right.read_bytes())
            self.assertIn('memory_engine/cli.py', a['file_sha256'])
            result = verify(left)
            self.assertTrue(result['smoke']['isolated_import'])
            self.assertTrue(result['cli'])

    def test_package_tamper_fails_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a.zip'
            build(path)
            path.write_bytes(path.read_bytes() + b'tamper')
            with self.assertRaisesRegex(AssertionError, 'SHA'):
                verify(path)

    def test_missing_cli_manifest_or_member_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a.zip'
            manifest = build(path)
            del manifest['file_sha256']['memory_engine/cli.py']
            path.with_suffix('.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(AssertionError, 'list mismatch'):
                verify(path)

    def test_archive_path_traversal_fails_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('../outside.py', 'bad')
            manifest = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'bytes': path.stat().st_size, 'files': 1,
                        'file_sha256': {'../outside.py': hashlib.sha256(b'bad').hexdigest()}}
            path.with_suffix('.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(AssertionError, 'unsafe'):
                verify(path)
            self.assertFalse((Path(directory).parent / 'outside.py').exists())


if __name__ == '__main__':
    unittest.main()
