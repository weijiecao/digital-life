"""控制面板后端：存储统计、备份轮转、维护、归档导出。"""

import json
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .recovery import cleanup_sidecars

DB_NAME = 'manual_test_memory.sqlite3'
BACKUP_NAME = re.compile(r'memory_\d{8}T\d{6}_[0-9a-f]{8}\.sqlite3')
DEFAULT_PER_MESSAGE = 11 * 1024
STANDARD_PER_MESSAGE = 11 * 1024
OPTIMIZED_PER_MESSAGE = int(4.5 * 1024)


def db_path(root):
    return Path(root) / DB_NAME


def _file_size(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def list_backups(root):
    folder = Path(root) / 'backups'
    if not folder.is_dir():
        return []
    items = []
    for path in sorted(folder.glob('*'), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.is_file() and not path.name.endswith(('-wal', '-shm')):
            items.append({'name': path.name, 'bytes': path.stat().st_size,
                          'at': datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds')})
    return items


def _latency(root, limit=500):
    path = Path(root) / 'latency.jsonl'
    if not path.is_file():
        return {'turns': 0, 'injected': 0, 'p50': None, 'p95': None, 'max': None}
    values = []
    injected = 0
    for line in path.read_text(encoding='utf-8').splitlines()[-limit:]:
        try:
            row = json.loads(line)
            values.append(float(row['ms']))
            injected += 1 if row.get('injected') else 0
        except (ValueError, KeyError, TypeError):
            continue
    if not values:
        return {'turns': 0, 'injected': 0, 'p50': None, 'p95': None, 'max': None}
    values.sort()
    return {'turns': len(values), 'injected': injected,
            'p50': round(values[len(values) // 2], 2),
            'p95': round(values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))], 2),
            'max': round(values[-1], 2)}


def stats(root, store):
    lock_path = Path(root) / '.memory.lock'
    lock_owner = ''
    if lock_path.is_file():
        try:
            lock_owner = lock_path.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            lock_owner = ''
    path = db_path(root)
    captures = 0
    pending = 0
    events = 0
    oldest = None
    recent = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        captures = store.flow.db.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
        pending = store.flow.db.execute(
            "SELECT COUNT(*) FROM dl_capture WHERE state='pending'").fetchone()[0]
        oldest = store.flow.db.execute(
            'SELECT MIN(recorded_at) FROM dl_capture').fetchone()[0]
        recent = store.flow.db.execute(
            'SELECT COUNT(*) FROM dl_capture WHERE recorded_at>=?', (cutoff,)).fetchone()[0]
        try:
            events = store.flow.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        except Exception:
            events = 0
    except Exception:
        pass
    db_bytes = _file_size(path)
    wal_bytes = _file_size(str(path) + '-wal')
    shm_bytes = _file_size(str(path) + '-shm')
    backups = list_backups(root)
    daily = round(recent / 7.0, 1)
    per_message = db_bytes / captures if captures else DEFAULT_PER_MESSAGE
    per_message = max(per_message, 1024)
    projections = {}
    for rate in (50, 100, 200, 300, 1000):
        projections[str(rate)] = {
            'db_gb': round(rate * 1825 * per_message / 1024 ** 3, 2),
            'with_backups_gb': round(rate * 1825 * per_message * 11 / 1024 ** 3, 2),
        }
    try:
        disk = shutil.disk_usage(root)
        disk_free_gb = round(disk.free / 1024 ** 3, 1)
        disk_total_gb = round(disk.total / 1024 ** 3, 1)
    except OSError:
        disk_free_gb = disk_total_gb = None
    latency = _latency(root)
    warnings = []
    if db_bytes > 5 * 1024 ** 3:
        warnings.append('数据库超过 5 GB，建议归档或优化向量存储')
    if wal_bytes > 256 * 1024 ** 2:
        warnings.append('WAL 超过 256 MB，建议执行 checkpoint')
    if not backups:
        warnings.append('没有任何备份，建议立即创建')
    elif time.time() - (Path(root) / 'backups').stat().st_mtime > 7 * 86400:
        warnings.append('最近备份超过 7 天')
    if latency['p95'] is not None and latency['p95'] > 1500:
        warnings.append('钩子 P95 超过 1.5 s')
    if disk_free_gb is not None and disk_total_gb and disk_free_gb / disk_total_gb < 0.2:
        warnings.append('磁盘剩余不足 20%')
    return {
        'db': {'path': str(path), 'bytes': db_bytes, 'mb': round(db_bytes / 1024 ** 2, 2),
               'wal_mb': round(wal_bytes / 1024 ** 2, 2), 'shm_mb': round(shm_bytes / 1024 ** 2, 2),
               'schema_version': getattr(store, 'schema_version', None),
               'lock_owner': lock_owner},
        'captures': captures, 'pending': pending, 'events': events, 'oldest': oldest,
        'daily_7d': daily, 'per_message_bytes': round(per_message),
        'projections_5y': projections,
        'backups': {'count': len(backups), 'items': backups[:10]},
        'latency': latency,
        'disk': {'free_gb': disk_free_gb, 'total_gb': disk_total_gb},
        'warnings': warnings,
        'per_message': {'standard': STANDARD_PER_MESSAGE, 'optimized': OPTIMIZED_PER_MESSAGE},
    }


def create_backup(store):
    name = store.backup()
    return {'name': name}


def rotate_backups(root, keep=7):
    keep = max(1, min(int(keep), 100))
    folder = Path(root) / 'backups'
    # 只把真正的备份文件算进 keep；-wal/-shm 不是备份，不能挤掉 .sqlite3。
    items = sorted((path for path in folder.glob('*')
                    if path.is_file() and BACKUP_NAME.fullmatch(path.name)),
                   key=lambda item: item.stat().st_mtime, reverse=True) \
        if folder.is_dir() else []
    removed = []
    cleaned = []
    for path in items[keep:]:
        try:
            path.unlink()
            removed.append(path.name)
            cleaned.extend(cleanup_sidecars(path))
        except OSError:
            continue
    # 保留的备份也清一次旁文件，兼容旧版本留下的残留。
    for path in items[:keep]:
        cleaned.extend(cleanup_sidecars(path))
    return {'kept': keep, 'removed': removed, 'cleaned': cleaned,
            'remaining': len(list_backups(root))}


def checkpoint(store):
    row = store.flow.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    return {'result': list(row) if row else []}


def integrity(store):
    row = store.flow.db.execute('PRAGMA integrity_check').fetchone()
    return {'result': row[0] if row else 'unknown'}


def archive(store, months=12, export=False):
    """导出早于 months 的原话；只读，不删除主库数据。"""
    months = max(1, min(int(months), 120))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30 * months)).isoformat()
    rows = store.flow.db.execute(
        'SELECT seq,content,content_sha,recorded_at,state FROM dl_capture '
        'WHERE recorded_at<? ORDER BY seq', (cutoff,)).fetchall()
    if not export:
        return {'cutoff': cutoff, 'count': len(rows), 'exported': False}
    folder = Path(store.root) / 'archives'
    folder.mkdir(parents=True, exist_ok=True)
    name = 'archive-%s.jsonl' % datetime.now().strftime('%Y%m%d-%H%M%S')
    path = folder / name
    with path.open('w', encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps({'seq': row['seq'], 'content': row['content'],
                                     'sha256': row['content_sha'],
                                     'recorded_at': row['recorded_at'],
                                     'state': row['state']}, ensure_ascii=False) + '\n')
    return {'cutoff': cutoff, 'count': len(rows), 'exported': True,
            'path': str(path), 'bytes': path.stat().st_size}


def vacuum(store):
    path = Path(store.root) / DB_NAME
    before = _file_size(path)
    store.flow.db.execute('VACUUM')
    after = _file_size(path)
    return {'before_mb': round(before / 1024 ** 2, 2), 'after_mb': round(after / 1024 ** 2, 2)}
