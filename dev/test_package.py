"""打包回归：zip 结构、版本、校验和、排除缓存。"""

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parent
PLUGIN = DEV_ROOT / 'astrbot_plugin_digital_memory_store'


def current_version():
    for line in (PLUGIN / 'metadata.yaml').read_text(encoding='utf-8').splitlines():
        if line.startswith('version:'):
            return line.split(':', 1)[1].strip()
    raise AssertionError('metadata.yaml missing version')



def load_build_module():
    spec = importlib.util.spec_from_file_location(
        'dlm_build_package', DEV_ROOT / 'scripts' / 'build_package.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPackage(unittest.TestCase):
    def setUp(self):
        self.module = load_build_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_zip_contains_required_files(self):
        result = self.module.build(self.out)
        archive = Path(result['archive'])
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
        prefix = 'astrbot_plugin_digital_memory_store/'
        for required in ('main.py', 'metadata.yaml', '_conf_schema.json',
                         'INSTALL.md', 'requirements.txt', 'presets/self.json'):
            self.assertIn(prefix + required, names)
        self.assertFalse(any('__pycache__' in name or name.endswith('.pyc') for name in names))
        self.assertEqual(result['version'], current_version())
        self.assertNotIn(prefix + '测试前功能清单.md', names)

    def test_checksum_matches(self):
        result = self.module.build(self.out)
        archive = Path(result['archive'])
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(digest, result['sha256'])
        checksum_text = Path(result['checksum']).read_text(encoding='utf-8')
        self.assertIn(digest, checksum_text)

    def test_build_is_repeatable(self):
        first = self.module.build(self.out)
        second = self.module.build(self.out)
        self.assertEqual(first['files'], second['files'])
        self.assertEqual(first['size_mb'], second['size_mb'])


if __name__ == '__main__':
    unittest.main()
