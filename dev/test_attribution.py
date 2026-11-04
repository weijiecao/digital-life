"""上下文归因工具的离线测试：只验证“插件片段/普通历史”分类。"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parent
TESTBED = DEV_ROOT.parent / 'testbed'


def load_module():
    spec = importlib.util.spec_from_file_location(
        'dlm_attribution', TESTBED / 'harness' / 'attribution.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request(secret, in_plugin, in_history=False):
    messages = [{'role': 'system', 'content': '系统提示'}]
    if in_history:
        messages.append({'role': 'user', 'content': '普通历史：' + secret})
    if in_plugin:
        messages.append({'role': 'user', 'content': [
            {'type': 'text', 'text': '[DL_MEMORY_V040] 未确认线索：' + secret}]})
    messages.append({'role': 'user', 'content': '问题'})
    return {'at': 1700000000.0, 'payload': {'messages': messages}}


class TestAttribution(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_only_plugin(self):
        summary, _ = self.module.analyse([request('蓝鲸7391', True)], '蓝鲸7391')
        self.assertEqual(summary['secret_only_plugin'], 1)
        self.assertEqual(summary['secret_in_history'], 0)

    def test_in_history(self):
        summary, _ = self.module.analyse([request('蓝鲸7391', True, True)], '蓝鲸7391')
        self.assertEqual(summary['secret_in_history'], 1)
        self.assertEqual(summary['secret_only_plugin'], 0)

    def test_absent(self):
        summary, _ = self.module.analyse([request('蓝鲸7391', False)], '蓝鲸7391')
        self.assertEqual(summary['secret_absent'], 1)

    def test_cli_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'llm.jsonl'
            log.write_text(json.dumps(request('蓝鲸7391', True), ensure_ascii=False) + '\n',
                           encoding='utf-8')
            out = Path(tmp) / 'out.json'
            code = self.module.main(['--secret', '蓝鲸7391', '--log', str(log),
                                     '--json', str(out)])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(data['summary']['secret_only_plugin'], 1)


if __name__ == '__main__':
    unittest.main()
