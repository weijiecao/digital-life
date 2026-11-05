"""归因工具：判断某条信息是插件注入的，还是 AstrBot 自带会话历史里本来就有的。

原理：插件注入的片段带固定标记（`[DL_MEMORY_V040]` / `背景上下文` / `待审记录序号`），
其余 messages 是 AstrBot 自带的系统提示与对话历史。给一个只出现在长程记忆里的“暗号”，
看它在请求体里出现在哪一侧：

- 只在插件片段里出现 → 这次上下文只能来自插件；
- 也出现在普通 messages 里 → AstrBot 自带窗口就能提供，无法归因给插件；
- 两边都没有、但模型答出来了 → 不是本插件提供的（或模型幻觉）。

用法：
    python harness/attribution.py --secret 蓝鲸7391
    python harness/attribution.py --secret 蓝鲸7391 --since 2026-11-04T12:00:00
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent


def emit(text):
    """把中文安全写到终端：英文 Windows 的控制台是 cp1252，直接 print 会 UnicodeEncodeError。"""
    try:
        print(text)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, 'buffer', None)
        payload = (text + '\n')
        if buffer is not None:
            buffer.write(payload.encode('utf-8', 'replace'))
            buffer.flush()
        else:
            sys.stdout.write(payload.encode('ascii', 'replace').decode('ascii'))
DEFAULT_LOG = TESTBED / 'logs' / 'llm_requests.jsonl'

MARKERS = ('[DL_MEMORY_V040]', '背景上下文', '待审记录序号', '未确认的联想', '候选筛选')


def _texts(value):
    """递归收集字符串。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


def _parts(message):
    """把一条 message 拆成 (是否为插件片段, 文本)。"""
    content = message.get('content')
    if isinstance(content, str):
        yield _is_plugin(content), content
        return
    if not isinstance(content, list):
        return
    for part in content:
        if isinstance(part, str):
            yield _is_plugin(part), part
        elif isinstance(part, dict):
            text = part.get('text')
            if isinstance(text, str):
                yield _is_plugin(text), text


def _is_plugin(text):
    return any(marker in text for marker in MARKERS)


def analyse(rows, secret, since=None):
    summary = {
        'requests': 0,
        'plugin_requests': 0,
        'secret_only_plugin': 0,
        'secret_in_history': 0,
        'secret_absent': 0,
        'last': None,
    }
    details = []
    for row in rows:
        at = row.get('at')
        if since is not None and isinstance(at, (int, float)):
            when = datetime.fromtimestamp(at)
            if when < since:
                continue
        payload = row.get('payload') or {}
        messages = payload.get('messages') or []
        plugin_texts = []
        history_texts = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            for is_plugin, text in _parts(message):
                (plugin_texts if is_plugin else history_texts).append(text)
        in_plugin = any(secret in text for text in plugin_texts)
        in_history = any(secret in text for text in history_texts)
        summary['requests'] += 1
        if plugin_texts:
            summary['plugin_requests'] += 1
        if in_plugin and not in_history:
            summary['secret_only_plugin'] += 1
        elif in_history:
            summary['secret_in_history'] += 1
        else:
            summary['secret_absent'] += 1
        details.append({'at': at, 'plugin_chars': sum(len(t) for t in plugin_texts),
                        'secret_in_plugin': in_plugin, 'secret_in_history': in_history})
        summary['last'] = details[-1]
    return summary, details


def render(summary):
    lines = ['# 归因分析', '',
             '| 项 | 值 |', '|---|---|',
             '| 请求数 | %d |' % summary['requests'],
             '| 含插件片段的请求 | %d |' % summary['plugin_requests'],
             '| 暗号只在插件片段 | %d |' % summary['secret_only_plugin'],
             '| 暗号在普通历史里 | %d |' % summary['secret_in_history'],
             '| 暗号两边都没有 | %d |' % summary['secret_absent'],
             '']
    if summary['last']:
        lines.append('最后一次请求：插件片段 %d 字；暗号在插件=%s，在历史=%s。'
                     % (summary['last']['plugin_chars'],
                        summary['last']['secret_in_plugin'],
                        summary['last']['secret_in_history']))
        lines.append('')
    lines.append('判读：')
    lines.append('- 若“暗号只在插件片段”>0，且模型回答复述了暗号 → 上下文来自本插件；')
    lines.append('- 若“暗号在普通历史里”，AstrBot 自带窗口就能提供，不能归因给插件；')
    lines.append('- 若两边都没有却答出 → 不是本插件提供（检查模型/其他记忆插件）。')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='插件上下文归因')
    parser.add_argument('--secret', required=True, help='只存在于长程记忆里的唯一暗号')
    parser.add_argument('--log', default=str(DEFAULT_LOG))
    parser.add_argument('--since', default='', help='只看该本地时间之后的请求')
    parser.add_argument('--json', default='')
    args = parser.parse_args(argv)
    rows = []
    for line in Path(args.log).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    since = datetime.fromisoformat(args.since) if args.since else None
    summary, details = analyse(rows, args.secret, since)
    emit(render(summary))
    if args.json:
        Path(args.json).write_text(json.dumps({'summary': summary, 'details': details},
                                              ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if summary['requests'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
