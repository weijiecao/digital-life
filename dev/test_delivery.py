"""交付层回归：配置预设合规、安装/升级/回退在临时 AstrBot 根目录里可复现。

不启动 AstrBot，只验证文件与配置落位；真正的插件加载由 TESTBED/集成测试覆盖。
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parent
PLUGIN = DEV_ROOT / 'astrbot_plugin_digital_memory_store'
SCHEMA = json.loads((PLUGIN / '_conf_schema.json').read_text(encoding='utf-8'))
PRESETS = ('off', 'self', 'debug')


def load_install_module():
    spec = importlib.util.spec_from_file_location(
        'dlm_install', DEV_ROOT / 'scripts' / 'install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPresets(unittest.TestCase):
    def test_every_preset_key_exists_in_schema_with_matching_type(self):
        expected = {'bool': bool, 'string': str, 'int': int}
        for name in PRESETS:
            preset = json.loads((PLUGIN / 'presets' / (name + '.json')).read_text(encoding='utf-8'))
            self.assertTrue(preset, name)
            for key, value in preset.items():
                self.assertIn(key, SCHEMA, '%s: %s' % (name, key))
                declared = SCHEMA[key]['type']
                if declared in expected:
                    self.assertIsInstance(value, expected[declared], '%s.%s' % (name, key))

    def test_off_preset_disables_plugin_and_nothing_else(self):
        preset = json.loads((PLUGIN / 'presets' / 'off.json').read_text(encoding='utf-8'))
        self.assertFalse(preset.get('enabled'))
        self.assertFalse(preset.get('always_context_enabled'))
        self.assertEqual(set(preset), {'enabled', 'always_context_enabled'})

    def test_self_preset_enables_automation_but_not_remote_or_drafts(self):
        preset = json.loads((PLUGIN / 'presets' / 'self.json').read_text(encoding='utf-8'))
        self.assertTrue(preset.get('automatic_memory_enabled'))
        self.assertNotIn('model_judge_enabled', preset)
        self.assertNotIn('auto_model_enabled', preset)
        self.assertNotIn('model_drafts_enabled', preset)
        for key in ('weak_association_enabled', 'semantic_source_context_enabled',
                    'multi_event_context_enabled'):
            self.assertNotIn(key, preset, '自用档不应默认打开实验开关')

    def test_debug_preset_opens_experiments_but_not_remote(self):
        preset = json.loads((PLUGIN / 'presets' / 'debug.json').read_text(encoding='utf-8'))
        for key in ('weak_association_enabled', 'semantic_source_context_enabled',
                    'multi_event_context_enabled', 'detail_priority_enabled',
                    'compact_context_enabled', 'event_priority_enabled'):
            self.assertTrue(preset.get(key), key)
        self.assertNotIn('model_judge_enabled', preset)
        self.assertNotIn('auto_model_enabled', preset)


def load_verify_module():
    spec = importlib.util.spec_from_file_location(
        'dlm_verify_install', DEV_ROOT / 'scripts' / 'verify_install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestVerifyInstall(unittest.TestCase):
    def setUp(self):
        self.install_module = load_install_module()
        self.verify_module = load_verify_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_installed_root_passes(self):
        model_dir = self.root / 'model'
        model_dir.mkdir()
        (model_dir / 'config.json').write_text('{}', encoding='utf-8')
        self.install_module.install(self.root, 'self', '123456', str(model_dir))
        result = self.verify_module.verify(self.root)
        self.assertTrue(result['ok'], result['problems'])
        version = None
        for line in (DEV_ROOT / 'astrbot_plugin_digital_memory_store' /
                     'metadata.yaml').read_text(encoding='utf-8').splitlines():
            if line.startswith('version:'):
                version = line.split(':', 1)[1].strip()
        self.assertEqual(result['version'], version)
        self.assertEqual(result['checks']['presets'], ['off', 'self', 'debug'])

    def test_missing_config_key_fails(self):
        self.install_module.install(self.root, 'self', '123456')
        config_path = self.root / 'data' / 'config' / (
            'astrbot_plugin_digital_memory_store_config.json')
        config = json.loads(config_path.read_text(encoding='utf-8'))
        config.pop('context_budget_chars')
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        result = self.verify_module.verify(self.root)
        self.assertFalse(result['ok'])
        self.assertTrue(any('缺 key' in item for item in result['problems']))

    def test_bom_config_is_accepted(self):
        self.install_module.install(self.root, 'self', '123456')
        config_path = self.root / 'data' / 'config' / (
            'astrbot_plugin_digital_memory_store_config.json')
        config = json.loads(config_path.read_text(encoding='utf-8'))
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8-sig')
        result = self.verify_module.verify(self.root)
        self.assertTrue(result['ok'], result['problems'])

    def test_enabled_without_allowlist_fails(self):
        self.install_module.install(self.root, 'self', '')
        result = self.verify_module.verify(self.root)
        self.assertFalse(result['ok'])
        self.assertTrue(any('allowed_sender_ids' in item for item in result['problems']))


class TestInstallUpgradeRollback(unittest.TestCase):
    def setUp(self):
        self.module = load_install_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_writes_plugin_and_config(self):
        result = self.module.install(self.root, 'self', '123456',
                                     'D:/models/bge-small-zh-v1.5')
        target = Path(result['target'])
        self.assertTrue((target / 'main.py').is_file())
        self.assertTrue((target / 'presets' / 'self.json').is_file())
        config = json.loads(Path(result['config']).read_text(encoding='utf-8'))
        self.assertTrue(config['enabled'])
        self.assertTrue(config['automatic_memory_enabled'])
        self.assertEqual(config['allowed_sender_ids'], '123456')
        self.assertEqual(config['model_dir'], 'D:/models/bge-small-zh-v1.5')
        self.assertFalse(config['model_judge_enabled'])
        self.assertEqual(set(config), set(SCHEMA), '配置必须覆盖全部 schema 项')

    def test_install_refuses_existing_without_force(self):
        self.module.install(self.root, 'off')
        with self.assertRaises(FileExistsError):
            self.module.install(self.root, 'self')
        result = self.module.install(self.root, 'self', force=True)
        config = json.loads(Path(result['config']).read_text(encoding='utf-8'))
        self.assertTrue(config['enabled'])

    def test_upgrade_backs_up_and_keeps_config(self):
        first = self.module.install(self.root, 'self', '123456')
        config_before = Path(first['config']).read_text(encoding='utf-8')
        marker = Path(first['target']) / 'marker.txt'
        marker.write_text('old-version', encoding='utf-8')
        result = self.module.upgrade(self.root)
        backup = Path(result['backup'])
        self.assertTrue((backup / 'marker.txt').is_file(), '旧文件必须进备份')
        self.assertEqual((backup / 'marker.txt').read_text(encoding='utf-8'), 'old-version')
        self.assertFalse((Path(result['target']) / 'marker.txt').exists(), '新版本不应带旧标记')
        self.assertEqual(Path(first['config']).read_text(encoding='utf-8'), config_before,
                         '升级不得改配置')

    def test_rollback_restores_backup(self):
        first = self.module.install(self.root, 'self', '123456')
        marker = Path(first['target']) / 'marker.txt'
        marker.write_text('old-version', encoding='utf-8')
        self.module.upgrade(self.root)
        result = self.module.rollback(self.root)
        restored = Path(result['target']) / 'marker.txt'
        self.assertTrue(restored.is_file())
        self.assertEqual(restored.read_text(encoding='utf-8'), 'old-version')

    def test_rollback_without_backup_fails_loudly(self):
        self.module.install(self.root, 'off')
        with self.assertRaises(FileNotFoundError):
            self.module.rollback(self.root)


if __name__ == '__main__':
    unittest.main()
