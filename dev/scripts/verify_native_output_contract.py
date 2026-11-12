"""用指定 AstrBot 源文件验证纯文本请求和保存协议，不启动 AstrBot。

message 模块原样加载；ProviderRequest 类从 entities.py AST 原样抽取，
仅避开未使用的媒体/供应商 SDK 导入。此验证不等于完整宿主联调。
"""
import argparse
import ast
import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_engine.engine import MemoryEngine
from memory_engine.output import MemoryOutputService
from memory_engine.token_budget import TokenCounter
from memory_output import AstrBotOutputAdapter, OutputRequest
from v1_5_consolidator.store import V15Store
from v1_5_consolidator.contracts import SourceEnvelope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--message-module', type=Path, required=True)
    parser.add_argument('--provider-module', type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('_astrbot_output_message', args.message_module)
    message = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = message
    spec.loader.exec_module(message)
    tree = ast.parse(args.provider_module.read_text(encoding='utf-8'))
    provider_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ProviderRequest')
    body = ast.parse('from __future__ import annotations').body + [provider_class]
    provider = ModuleType('_astrbot_output_provider')
    sys.modules[provider.__name__] = provider
    provider.__dict__.update(dataclass=dataclass, field=field)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 str(args.provider_module), 'exec'), provider.__dict__)
    with tempfile.TemporaryDirectory() as directory:
        store = V15Store(Path(directory) / 'memory.db')
        try:
            engine = MemoryEngine(store)
            text = '周六去青岛海边散步，带了风筝。'
            engine.ingest('scope1', SourceEnvelope('scope1', 1, hashlib.sha256(text.encode()).hexdigest(), 't', text),
                          {'narrator':'user','subject':'user','reality_status':'real'})
            counter = TokenCounter()
            service = MemoryOutputService(engine, counter,
                                          AstrBotOutputAdapter(lambda text: message.TextPart(text=text)))
            expected = OutputRequest('contract1', 'scope1', '青岛海边散步', 512)
            req = provider.ProviderRequest(prompt=expected.query, system_prompt='保持人格设定',
                                           contexts=[{'role':'user','content':'原始历史'}])
            req.extra_user_content_parts.append(message.TextPart(text='其他插件内容'))
            output = service.prepare(expected)
            first = service.inject(req, output, expected)
            repeat = service.inject(req, output, expected)
            assert first.status == 'INJECTED' and repeat.status == 'UNCHANGED'
            assembled = asyncio.run(req.assemble_context())
            assert any(p.get('_no_save') is True and p.get('text') == output.text for p in assembled['content'])
            model_message = message.Message.model_validate(assembled)
            provider_data = model_message.model_dump()
            assert any(p.get('text') == output.text for p in provider_data['content'])
            saved = message.dump_messages_with_checkpoints([model_message])
            saved_text = json.dumps(saved, ensure_ascii=False)
            assert all(p.get('text') != output.text for record in saved for p in record['content'])
            assert '风筝' not in saved_text
            assert '其他插件内容' in saved_text and expected.query in saved_text
            assert req.system_prompt == '保持人格设定'
            assert req.contexts == [{'role':'user','content':'原始历史'}]
            result = {'verdict':'PASS', 'validation':'source-extracted ProviderRequest + unmodified message module',
                      'provider_sees_memory':True, 'persisted_history_excludes_memory':True,
                      'unrelated_content_preserved':True, 'repeat_deduplicated':True,
                      'used_tokens':output.used_tokens, 'budget_tokens':output.budget_tokens,
                      'tokenizer':counter.encoding_name,
                      'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in (args.message_module,args.provider_module)}}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            store.close()

if __name__ == '__main__':
    main()
