"""L0 soak 记录器：定时把运行事实写进 JSONL，供 3 天连续运行后做判定。

采集项（每 tick）：
  - 插件库：原话总数/待审/已关联、缺口总数、后台任务 done/pending/dead、库文件大小
  - 进程：serve.py 的 RSS 与 PID、6201 连接数、9101 mock 端口
  - 日志：serve.log 新增错误行数
  - provider：请求总数、含记忆片段（[DL_MEMORY_V040]）的请求数

用法：
    python harness/soak.py --interval 300                    # 每 5 分钟，一直跑
    python harness/soak.py --once                            # 采一次就退出（自检/测试）
    python harness/soak.py --interval 600 --hours 72 --out logs/soak.jsonl

产物：
    logs/soak.jsonl      每次一行
    logs/soak_summary.json 汇总（每次覆盖写，崩溃后仍可读）
"""

import argparse
import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
INSTANCE = TESTBED / 'instance'
PLUGIN_DATA = INSTANCE / 'data/plugin_data/astrbot_plugin_digital_memory_store'
DB = PLUGIN_DATA / 'manual_test_memory.sqlite3'
SERVE_LOG = TESTBED / 'logs/serve.log'
MOCK_LOG = TESTBED / 'logs/llm_requests.jsonl'
LATENCY_LOG = PLUGIN_DATA / 'latency.jsonl'
PREFIX = '[DL_MEMORY_V040]'


def _db_metrics(db_path=DB):
    if not db_path.is_file():
        return {'available': False}
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            captures = conn.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM dl_capture WHERE state='pending'").fetchone()[0]
            linked = conn.execute(
                "SELECT COUNT(*) FROM dl_capture WHERE state='linked'").fetchone()[0]
            gaps = conn.execute(
                'SELECT COALESCE(SUM(total),0) FROM dl_capture_gaps').fetchone()[0]
            jobs = {row[0]: row[1] for row in conn.execute(
                'SELECT status,COUNT(*) FROM dl_auto_jobs GROUP BY status')}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {'available': False, 'error': repr(exc)}
    size = sum(path.stat().st_size for path in (
        Path(str(db_path)), Path(str(db_path) + '-wal'), Path(str(db_path) + '-shm'))
        if path.exists())
    return {'available': True, 'captures': captures, 'pending': pending,
            'linked': linked, 'gaps': gaps, 'jobs': jobs,
            'db_size_mb': round(size / 1024 / 1024, 2)}


def _serve_process():
    try:
        import psutil
    except ImportError:
        return None
    for process in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
        try:
            cmdline = ' '.join(process.info.get('cmdline') or [])
            if 'harness' in cmdline and 'serve.py' in cmdline:
                return {'pid': process.info['pid'],
                        'rss_mb': round(process.info['memory_info'].rss / 1024 / 1024, 1)}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _port(port, host='127.0.0.1'):
    sock = socket.socket()
    sock.settimeout(0.5)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _count_lines(path, start=0):
    if not path.is_file():
        return 0, 0, 0, 0
    total = injected = errors = 0
    offset = start
    with path.open('rb') as handle:
        handle.seek(start)
        for raw in handle:
            offset += len(raw)
            line = raw.decode('utf-8', 'replace')
            total += 1
            if PREFIX in line:
                injected += 1
            if 'ERROR' in line or 'Traceback' in line or 'Failed to import' in line:
                errors += 1
    return total, injected, errors, offset


def _latency_metrics(path, start=0):
    """读插件每轮写的 latency.jsonl，返回本 tick 的回合数/注入数/P50/P95。"""
    if not path.is_file():
        return {'turns': 0, 'injected': 0, 'p50': None, 'p95': None, 'max': None}, start
    values = []
    injected = 0
    offset = start
    with path.open('rb') as handle:
        handle.seek(start)
        for raw in handle:
            offset += len(raw)
            try:
                row = json.loads(raw.decode('utf-8'))
                values.append(float(row['ms']))
                injected += 1 if row.get('injected') else 0
            except (ValueError, KeyError, TypeError):
                continue
    if not values:
        return {'turns': 0, 'injected': 0, 'p50': None, 'p95': None, 'max': None}, offset
    values.sort()
    return ({'turns': len(values), 'injected': injected,
             'p50': round(values[len(values) // 2], 2),
             'p95': round(values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))], 2),
             'max': round(values[-1], 2)}, offset)


def collect(paths=None, offsets=None):
    paths = paths or {}
    offsets = offsets or {}
    db_path = Path(paths.get('db', DB))
    serve_log = Path(paths.get('serve_log', SERVE_LOG))
    mock_log = Path(paths.get('mock_log', MOCK_LOG))
    latency_log = Path(paths.get('latency', LATENCY_LOG))
    metrics = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'db': _db_metrics(db_path)}
    metrics['serve'] = _serve_process()
    metrics['ports'] = {'reverse_ws': _port(6201), 'mock': _port(9101)}
    serve_total, _, serve_errors, serve_offset = _count_lines(
        serve_log, offsets.get('serve_offset', 0))
    mock_total, mock_injected, _, mock_offset = _count_lines(
        mock_log, offsets.get('mock_offset', 0))
    latency, latency_offset = _latency_metrics(
        latency_log, offsets.get('latency_offset', 0))
    metrics['logs'] = {'serve_new_lines': serve_total, 'serve_new_errors': serve_errors,
                       'provider_new_requests': mock_total,
                       'provider_new_injected': mock_injected}
    metrics['latency'] = latency
    metrics['offsets'] = {'serve_offset': serve_offset, 'mock_offset': mock_offset,
                          'latency_offset': latency_offset}
    return metrics


def summarize(rows):
    if not rows:
        return {'ticks': 0}
    first, last = rows[0], rows[-1]
    pids = {row.get('serve', {}).get('pid') if row.get('serve') else None for row in rows}
    pids.discard(None)
    restarts = 0
    previous = None
    for row in rows:
        pid = row.get('serve', {}).get('pid') if row.get('serve') else None
        if previous is not None and pid is not None and pid != previous:
            restarts += 1
        if pid is not None:
            previous = pid
    try:
        first_captures = first['db'].get('captures', 0)
        last_captures = last['db'].get('captures', 0)
        first_gaps = first['db'].get('gaps', 0)
        last_gaps = last['db'].get('gaps', 0)
    except KeyError:
        first_captures = last_captures = first_gaps = last_gaps = 0
    requests = sum(row.get('logs', {}).get('provider_new_requests', 0) for row in rows)
    injected = sum(row.get('logs', {}).get('provider_new_injected', 0) for row in rows)
    errors = sum(row.get('logs', {}).get('serve_new_errors', 0) for row in rows)
    latency_turns = sum((row.get('latency') or {}).get('turns', 0) for row in rows)
    latency_injected = sum((row.get('latency') or {}).get('injected', 0) for row in rows)
    p95s = [(row.get('latency') or {}).get('p95') for row in rows
            if (row.get('latency') or {}).get('p95') is not None]
    p50s = [(row.get('latency') or {}).get('p50') for row in rows
            if (row.get('latency') or {}).get('p50') is not None]
    rss = [row['serve']['rss_mb'] for row in rows
           if row.get('serve') and row['serve'].get('rss_mb') is not None]
    return {
        'ticks': len(rows), 'start': first.get('ts'), 'last': last.get('ts'),
        'captures_start': first_captures, 'captures_end': last_captures,
        'captures_delta': last_captures - first_captures,
        'gaps_start': first_gaps, 'gaps_end': last_gaps,
        'gaps_delta': last_gaps - first_gaps,
        'provider_requests': requests, 'provider_injected': injected,
        'injection_rate': round(injected / requests, 3) if requests else None,
        'serve_error_lines': errors,
        'hook_turns': latency_turns,
        'hook_injected': latency_injected,
        'hook_injection_rate': (round(latency_injected / latency_turns, 3)
                                if latency_turns else None),
        'hook_p50_last': p50s[-1] if p50s else None,
        'hook_p95_max': max(p95s) if p95s else None,
        'restarts': restarts,
        'rss_mb_max': max(rss) if rss else None,
        'rss_mb_last': rss[-1] if rss else None,
        'connected_last': bool(last.get('ports', {}).get('reverse_ws')),
        'mock_last': bool(last.get('ports', {}).get('mock')),
        'dead_jobs_last': last.get('db', {}).get('jobs', {}).get('dead', 0),
        'pending_jobs_last': last.get('db', {}).get('jobs', {}).get('pending', 0),
    }


def _log(message):
    """pythonw 下 sys.stdout 为 None：只在有控制台时打印，绝不因输出失败而退出。"""
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(message, flush=True)
    except (OSError, ValueError):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='L0 soak 记录器')
    parser.add_argument('--interval', type=int, default=300, help='采样间隔秒（≥30）')
    parser.add_argument('--hours', type=float, default=0, help='运行时长上限；0 表示一直跑')
    parser.add_argument('--out', default=str(TESTBED / 'logs' / 'soak.jsonl'))
    parser.add_argument('--summary', default=str(TESTBED / 'logs' / 'soak_summary.json'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    if args.interval < 30:
        raise SystemExit('--interval 必须 ≥30 秒')

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    summary_path = Path(args.summary)
    offsets = {}
    rows = []
    deadline = time.monotonic() + args.hours * 3600 if args.hours else None
    try:
        while True:
            metrics = collect(offsets=offsets)
            offsets = metrics.pop('offsets')
            rows.append(metrics)
            with out.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(metrics, ensure_ascii=False) + '\n')
            summary = summarize(rows)
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                    encoding='utf-8')
            _log('[%s] captures=%s pending=%s errors=%s injected=%s/%s'
                 % (metrics['ts'], metrics['db'].get('captures'),
                    metrics['db'].get('pending'), metrics['logs']['serve_new_errors'],
                    metrics['logs']['provider_new_injected'],
                    metrics['logs']['provider_new_requests']))
            if args.once:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
