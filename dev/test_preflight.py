"""依赖与模型自检的离线测试：只验证提示文案与判定分级，不导入 torch。"""
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_digital_memory_store.preflight import check_environment, format_report


def has(*available):
    present = set(available)
    return lambda name: name in present


class TestPreflight(unittest.TestCase):
    def test_missing_dependencies_produce_one_actionable_message(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'config.json').write_text('{}', encoding='utf-8')
            result = check_environment(d, has_module=has())
        self.assertFalse(result['ok'])
        self.assertFalse(result['semantic_ready'])
        self.assertIn('torch', result['message'])
        self.assertIn('sentence-transformers', result['message'])
        self.assertIn('pip install -r requirements.txt', result['message'])
        self.assertIn('记录、查看、备份不需要 BGE', result['message'])
        self.assertNotIn('\n', result['message'], '提示必须是单条，便于日志定位')

    def test_empty_model_dir_is_not_an_error(self):
        result = check_environment('', has_module=has())
        self.assertTrue(result['ok'])
        self.assertFalse(result['semantic_ready'])
        self.assertEqual(result['message'], '')
        self.assertIn('语义检索关闭', format_report(result))

    def test_invalid_model_dir_is_reported_with_path_and_fix(self):
        with tempfile.TemporaryDirectory() as d:
            result = check_environment(d, has_module=has('torch', 'sentence_transformers'))
        self.assertFalse(result['ok'])
        self.assertFalse(result['semantic_ready'])
        self.assertIn('config.json', result['message'])
        self.assertIn(d, result['message'])
        self.assertIn('model_dir', result['message'])

    def test_missing_directory_is_reported(self):
        missing = str(Path(tempfile.gettempdir()) / 'dl-memory-no-such-model-dir')
        result = check_environment(missing, has_module=has('torch', 'sentence_transformers'))
        self.assertFalse(result['ok'])
        self.assertIn('模型目录不存在', result['message'])

    def test_valid_environment_is_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'config.json').write_text('{}', encoding='utf-8')
            result = check_environment(d, has_module=has('torch', 'sentence_transformers'))
        self.assertTrue(result['ok'])
        self.assertTrue(result['semantic_ready'])
        self.assertEqual(result['message'], '')
        self.assertIn('语义检索：可用', format_report(result))

    def test_report_lists_every_check(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'config.json').write_text('{}', encoding='utf-8')
            result = check_environment(d, has_module=has('torch'))
        rendered = format_report(result)
        self.assertIn('依赖与模型自检', rendered)
        self.assertIn('[失败]', rendered)
        self.assertIn('sentence-transformers', rendered)


if __name__ == '__main__':
    unittest.main()
