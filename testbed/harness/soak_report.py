"""L0 soak 汇总：把 logs/soak.jsonl 聚合成六条验收线，并给出可判定的结论。

用法：
    python harness/soak_report.py                       # 读全部 soak.jsonl
    python harness/soak_report.py --sent 3:20,4:20      # 每天发送条数（日:条）
    python harness/soak_report.py --require-hours 72 --out logs/soak_report.json

说明：
- 采集成功率 = captures_delta / 发送条数（同一天内）；没有 --sent 时只报采集增量。
- 注入率 = hook_injected / hook_turns；hook 数据来自插件 latency.jsonl。
- 延迟 P95 取各 tick 的 hook_p95_max 的最大值。
- 结论只对“有数据”的项下判定；缺数据的项标“需人工/未采集”。
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
DEFAULT_INPUT = TESTBED / 'logs' / 'soak.jsonl'
DEFAULT_OUT = TESTBED / 'logs' / 'soak_report.json'


def load_rows(path=DEFAULT_INPUT):
    rows = []
    if not Path(path).is_file():
        return rows
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _ts(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def aggregate(rows):
    if not rows:
        return {'ticks': 0, 'ok': False, 'reason': 'no data'}
    first, last = rows[0], rows[-1]
    start, end = _ts(first.get('ts')), _ts(last.get('ts'))
    hours = round((end - start).total_seconds() / 3600, 2) if start and end else None
    captures = [row.get('db', {}).get('captures', 0) for row in rows]
    gaps = [row.get('db', {}).get('gaps', 0) for row in rows]
    hook_turns = sum((row.get('latency') or {}).get('turns', 0) for row in rows)
    hook_injected = sum((row.get('latency') or {}).get('injected', 0) for row in rows)
    p95s = [(row.get('latency') or {}).get('p95') for row in rows
            if (row.get('latency') or {}).get('p95') is not None]
    errors = sum(row.get('logs', {}).get('serve_new_errors', 0) for row in rows)
    pids = [row.get('serve', {}).get('pid') for row in rows if row.get('serve')]
    restarts = sum(1 for a, b in zip(pids, pids[1:]) if a and b and a != b)
    connected = [bool((row.get('ports') or {}).get('reverse_ws')) for row in rows]
    connected_ratio = round(sum(connected) / len(connected), 3) if connected else None
    rss = [(row.get('serve') or {}).get('rss_mb') for row in rows
           if row.get('serve') and row.get('serve', {}).get('rss_mb') is not None]
    # tick 间隔是否稳定（>2x 中位间隔视为断档）
    intervals = []
    for a, b in zip(rows, rows[1:]):
        ta, tb = _ts(a.get('ts')), _ts(b.get('ts'))
        if ta and tb:
            intervals.append((tb - ta).total_seconds())
    intervals.sort()
    median = intervals[len(intervals) // 2] if intervals else None
    stalls = sum(1 for value in intervals if median and value > 2 * median) if median else 0
    return {
        'ticks': len(rows), 'start': first.get('ts'), 'last': last.get('ts'),
        'hours': hours,
        'captures_start': captures[0], 'captures_end': captures[-1],
        'captures_delta': captures[-1] - captures[0],
        'gaps_delta': gaps[-1] - gaps[0],
        'hook_turns': hook_turns, 'hook_injected': hook_injected,
        'hook_injection_rate': round(hook_injected / hook_turns, 3) if hook_turns else None,
        'hook_p95_max': max(p95s) if p95s else None,
        'serve_error_lines': errors,
        'restarts': restarts,
        'connected_ratio': connected_ratio,
        'rss_mb_max': max(rss) if rss else None,
        'tick_interval_s': median,
        'tick_stalls': stalls,
    }


def judge(agg, sent=None, require_hours=72):
    """六条线里能自动判定的项；不能自动判的标 manual。"""
    checks = {}
    checks['采集增量'] = {'value': agg.get('captures_delta'), 'manual': sent is None,
                       'detail': '需 --sent 才能算成功率'}
    if sent:
        total_sent = sum(sent.values())
        rate = round(agg['captures_delta'] / total_sent, 3) if total_sent else None
        checks['采集成功率'] = {'value': rate, 'pass': rate is not None and rate >= 0.99}
    checks['注入率'] = {'value': agg.get('hook_injection_rate'),
                     'pass': (agg.get('hook_injection_rate') or 0) >= 0.95 if agg.get('hook_turns') else None}
    checks['勾子P95'] = {'value': agg.get('hook_p95_max'),
                      'pass': (agg.get('hook_p95_max') or 0) < 1500 if agg.get('hook_p95_max') is not None else None}
    checks['连接率'] = {'value': agg.get('connected_ratio'),
                     'pass': (agg.get('connected_ratio') or 0) >= 0.95 if agg.get('connected_ratio') is not None else None}
    checks['错误行'] = {'value': agg.get('serve_error_lines'),
                     'pass': (agg.get('serve_error_lines') or 0) == 0}
    checks['覆盖时长'] = {'value': agg.get('hours'),
                     'pass': (agg.get('hours') or 0) >= require_hours}
    hard = [item for item in checks.values() if item.get('pass') is False]
    return {'checks': checks, 'blocking': len(hard),
            'ok': agg.get('ticks', 0) > 0 and len(hard) == 0}


def render(agg, verdict, sent=None):
    lines = ['# L0 soak 汇总', '',
             '| 项 | 值 | 判定 |', '|---|---|---|']
    for name, item in verdict['checks'].items():
        if item.get('manual'):
            mark = '需人工（提供 --sent）'
        elif item.get('pass') is True:
            mark = '通过'
        elif item.get('pass') is False:
            mark = '**未通过**'
        else:
            mark = '未采集'
        lines.append('| %s | %s | %s |' % (name, item.get('value'), mark))
    lines.append('')
    lines.append('覆盖：%s → %s（%.1f 小时，%d 个 tick，间隔 %s s，断档 %s 次）'
                 % (agg.get('start'), agg.get('last'), agg.get('hours') or 0,
                    agg.get('ticks'), agg.get('tick_interval_s'), agg.get('tick_stalls')))
    lines.append('')
    lines.append('结论：%s' % ('**通过（有数据的项全部达标）**' if verdict['ok']
                             else '未通过（%d 项阻塞）' % verdict['blocking']))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='L0 soak 汇总')
    parser.add_argument('--input', default=str(DEFAULT_INPUT))
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    parser.add_argument('--require-hours', type=float, default=72)
    parser.add_argument('--sent', default='', help='每天发送条数：日:条,日:条')
    args = parser.parse_args(argv)
    sent = {}
    if args.sent:
        for item in args.sent.split(','):
            day, count = item.split(':')
            sent[day.strip()] = int(count)
    rows = load_rows(args.input)
    agg = aggregate(rows)
    verdict = judge(agg, sent or None, args.require_hours)
    text = render(agg, verdict, sent or None)
    Path(args.out).write_text(json.dumps({'aggregate': agg, 'verdict': verdict},
                                         ensure_ascii=False, indent=2), encoding='utf-8')
    print(text)
    print('已写入', args.out)
    return 0 if verdict['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
