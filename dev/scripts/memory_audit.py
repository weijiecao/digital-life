"""只读记忆审计器（V1）：把“注入可溯源、原文可校验、派生不越权”变成可复跑检查。

设计原则：
- 只读打开 SQLite（``mode=ro``），不写库、不调用模型、不发网络；
- 数据库检查与 packet 检查分开：前者查完整性/孤儿/时间，后者查“每个注入项能否指回原话”；
- 任何一条无法归类的注入字符串都算 violation，不用“看起来像模板”放过。

用法::

    python scripts/memory_audit.py --db <manual_test_memory.sqlite3>
    python scripts/memory_audit.py --root <plugin_data/...> --json
    python scripts/memory_audit.py --db <db> --packets <packets.jsonl> --json

packets.jsonl 每行：``{"scope": "...", "text": "[DL_MEMORY_V040] ...", "request_time": "..."}``
其中 ``text`` 可带固定头；不带也接受。

退出码：0 = 无 error；1 = 有 error；2 = 参数/打开失败。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PREFIX = '[DL_MEMORY_V040]'
MAX_CAPTURE = 4000
QUOTE_KEYS = {'原话', '未整理历史原话'}
TITLE_KEYS = {'未确认的联想事件', '未确认的联想候选'}
SEQ_KEYS = {'待审记录序号', '待审相关序号', '序号', '原话ID'}
TEMPLATE_KEYS = {
    '说明', '匹配方式', '线索方式', '背景上下文', '索引提示', '排序提示',
    '关联历史', '未确认的联想事件', '未确认的联想候选', '最近原话',
}
TIME_FUTURE_TOLERANCE = timedelta(minutes=5)

SEQ_TABLES = (
    ('dl_auto_jobs', 'seq'),
    ('dl_auto_groups', 'seq'),
    ('dl_auto_chains', 'seq'),
    ('dl_auto_localization', 'seq'),
    ('dl_auto_source_vectors', 'seq'),
    ('dl_auto_vector_chunks', 'seq'),
    ('dl_auto_vector_failures', 'seq'),
    ('dl_auto_text_index', 'seq'),
    ('dl_auto_text_grams', 'seq'),
    ('dl_auto_text_dirty', 'seq'),
    ('dl_auto_topic_members', 'seq'),
    ('dl_auto_model_jobs', 'seq'),
    ('dl_pending_index', 'seq'),
    ('dl_pending_terms', 'seq'),
    ('dl_pending_dirty', 'seq'),
    ('dl_suggestions', 'capture_seq'),
    ('dl_new_title_suggestions', 'capture_seq'),
)


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def audit_database(db_path, *, scope=None, now=None):
    """纯数据库检查；返回 dict（errors/warnings/info）。"""
    now = now or datetime.now(timezone.utc)
    errors, warnings, info = [], [], []
    conn = sqlite3.connect('file:%s?mode=ro' % Path(db_path).as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            errors.append({'check': 'integrity', 'detail': integrity})

        where, params = '', ()
        if scope is not None:
            where, params = ' WHERE scope=?', (scope,)
        rows = conn.execute(
            'SELECT seq,scope,content,content_sha,recorded_at,state FROM dl_capture' + where +
            ' ORDER BY scope,seq', params).fetchall()
        captures = [dict(row) for row in rows]
        info.append({'check': 'capture_rows', 'count': len(captures)})

        by_scope = {}
        duplicate_content = Counter()
        for row in captures:
            key = (row['scope'], row['content'])
            duplicate_content[key] += 1
            by_scope.setdefault(row['scope'], []).append(row)
            label = '%s#%s' % (row['scope'][:12], row['seq'])
            if not isinstance(row['content'], str) or not row['content']:
                errors.append({'check': 'capture_content', 'row': label, 'detail': 'empty'})
                continue
            if len(row['content']) > MAX_CAPTURE:
                errors.append({'check': 'capture_content', 'row': label,
                               'detail': 'length %d > %d' % (len(row['content']), MAX_CAPTURE)})
            if '\x00' in row['content']:
                errors.append({'check': 'capture_content', 'row': label, 'detail': 'NUL'})
            if row['content_sha'] != _sha(row['content']):
                errors.append({'check': 'capture_sha', 'row': label,
                               'detail': 'sha mismatch'})
            stamp = _parse_time(row['recorded_at'])
            if stamp is None:
                errors.append({'check': 'capture_time', 'row': label,
                               'detail': 'invalid/naive: %r' % row['recorded_at']})
            elif stamp > now + TIME_FUTURE_TOLERANCE:
                warnings.append({'check': 'capture_time_future', 'row': label,
                                 'detail': row['recorded_at']})
            if row['state'] not in ('pending', 'linked'):
                warnings.append({'check': 'capture_state', 'row': label,
                                 'detail': row['state']})

        dup = [{'scope': s, 'count': n, 'content': c[:40]}
               for (s, c), n in duplicate_content.items() if n > 1]
        if dup:
            info.append({'check': 'duplicate_content', 'count': len(dup), 'sample': dup[:5]})

        if _table_exists(conn, 'dl_auto_jobs'):
            bad = conn.execute(
                'SELECT COUNT(*) FROM dl_auto_jobs j WHERE NOT EXISTS ('
                'SELECT 1 FROM dl_capture c WHERE c.scope=j.scope AND c.seq=j.seq)').fetchone()[0]
            if bad:
                errors.append({'check': 'orphan_jobs', 'count': bad})
            status = {row[0]: row[1] for row in conn.execute(
                'SELECT status,COUNT(*) FROM dl_auto_jobs GROUP BY status')}
            over = conn.execute('SELECT COUNT(*) FROM dl_auto_jobs WHERE attempts>8').fetchone()[0]
            if over:
                warnings.append({'check': 'job_attempts', 'count': over})
            info.append({'check': 'jobs', 'by_status': status})

        for table, column in SEQ_TABLES:
            if not _table_exists(conn, table):
                continue
            try:
                bad = conn.execute(
                    'SELECT COUNT(*) FROM %s t WHERE NOT EXISTS ('
                    'SELECT 1 FROM dl_capture c WHERE c.scope=t.scope AND c.seq=t.%s)'
                    % (table, column)).fetchone()[0]
            except sqlite3.DatabaseError:
                continue
            if bad:
                errors.append({'check': 'orphan_derived', 'table': table, 'count': bad})

        if _table_exists(conn, 'dl_capture_gaps'):
            for row in conn.execute('SELECT scope,unresolved,total,last_at FROM dl_capture_gaps'):
                label = row['scope'][:12]
                if row['unresolved'] < 0 or row['total'] < 0 or row['unresolved'] > row['total']:
                    errors.append({'check': 'gap_counters', 'row': label,
                                   'detail': dict(row)})
                if _parse_time(row['last_at']) is None:
                    errors.append({'check': 'gap_time', 'row': label,
                                   'detail': row['last_at']})
                info.append({'check': 'gap', 'scope': label,
                             'unresolved': row['unresolved'], 'total': row['total']})

        if scope is None:
            scopes = {row['scope'] for row in captures}
            info.append({'check': 'scopes', 'count': len(scopes)})
    finally:
        conn.close()
    return {'db': str(db_path), 'errors': errors, 'warnings': warnings, 'info': info}


def _walk(value, path=(), out=None):
    """收集 (path_tuple, key, value)，便于按字段名分类。"""
    if out is None:
        out = []
    if isinstance(value, dict):
        for key, item in value.items():
            _walk(item, path + (str(key),), out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, path + ('#%d' % index,), out)
    else:
        out.append((path, path[-1] if path else '', value))
    return out


def audit_packet(captures_by_scope, all_by_content, scope, text, request_time=None):
    """O1：检查一个注入 packet 的每个原话/序号能否指回同 scope 的 dl_capture。"""
    errors, warnings, info = [], [], []
    request_time = request_time or datetime.now(timezone.utc)
    if isinstance(request_time, str):
        parsed = _parse_time(request_time)
        request_time = parsed or request_time
    if not isinstance(request_time, datetime):
        request_time = datetime.now(timezone.utc)

    body = text or ''
    if body.startswith(PREFIX):
        body = body.split('\n', 1)[1] if '\n' in body else ''
    try:
        packet = json.loads(body)
    except (TypeError, ValueError) as exc:
        return {'errors': [{'check': 'packet_json', 'detail': str(exc)}],
                'warnings': [], 'info': [], 'quotes': 0, 'titles': 0, 'seq_refs': 0}

    captures = captures_by_scope.get(scope, [])
    quotes = titles = seq_refs = 0
    for path, key, value in _walk(packet):
        leaf_key = path[-1] if path else ''
        in_quote_context = any(part in QUOTE_KEYS for part in path[:-1])
        if leaf_key in QUOTE_KEYS or (leaf_key == '内容' and in_quote_context):
            if not isinstance(value, str):
                continue
            quotes += 1
            match = next((row for row in captures if value and value in row['content']), None)
            exact = bool(match and match['content'] == value)
            if match:
                if not exact:
                    warnings.append({'check': 'quote_excerpt', 'value': value[:40],
                                     'seq': match['seq']})
                stamp = _parse_time(match['recorded_at'])
                if stamp is None or stamp >= request_time:
                    errors.append({'check': 'quote_time', 'value': value[:40],
                                   'seq': match['seq'], 'recorded_at': match['recorded_at'],
                                   'request_time': request_time.isoformat()})
            else:
                other = all_by_content.get(value)
                if other is not None and other != scope:
                    errors.append({'check': 'cross_scope', 'value': value[:40],
                                   'other_scope': other[:12]})
                else:
                    errors.append({'check': 'unclassified_quote', 'value': value[:60],
                                   'path': '.'.join(path)})
        elif leaf_key in TITLE_KEYS:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, str):
                    titles += 1
        elif leaf_key in SEQ_KEYS:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, int):
                    seq_refs += 1
                    if not any(row['seq'] == item for row in captures):
                        errors.append({'check': 'seq_missing', 'seq': item,
                                       'path': '.'.join(path)})

    return {'errors': errors, 'warnings': warnings, 'info': info,
            'quotes': quotes, 'titles': titles, 'seq_refs': seq_refs}


def load_captures(db_path):
    conn = sqlite3.connect('file:%s?mode=ro' % Path(db_path).as_posix(), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            'SELECT seq,scope,content,content_sha,recorded_at FROM dl_capture')]
    finally:
        conn.close()
    by_scope, by_content = {}, {}
    for row in rows:
        by_scope.setdefault(row['scope'], []).append(row)
        by_content.setdefault(row['content'], row['scope'])
    return by_scope, by_content


def audit_packets(db_path, packets_path):
    by_scope, by_content = load_captures(db_path)
    total = {'packets': 0, 'quotes': 0, 'titles': 0, 'seq_refs': 0,
             'errors': 0, 'warnings': 0, 'cross_scope': 0, 'unclassified': 0,
             'future': 0, 'seq_missing': 0, 'excerpt': 0}
    samples = []
    with open(packets_path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            result = audit_packet(by_scope, by_content, record.get('scope', ''),
                                  record.get('text', ''), record.get('request_time'))
            total['packets'] += 1
            total['quotes'] += result['quotes']
            total['titles'] += result['titles']
            total['seq_refs'] += result['seq_refs']
            total['errors'] += len(result['errors'])
            total['warnings'] += len(result['warnings'])
            for item in result['errors']:
                if item['check'] == 'cross_scope':
                    total['cross_scope'] += 1
                elif item['check'] == 'unclassified_quote':
                    total['unclassified'] += 1
                elif item['check'] == 'quote_time':
                    total['future'] += 1
                elif item['check'] == 'seq_missing':
                    total['seq_missing'] += 1
                if len(samples) < 30:
                    samples.append(item)
            for item in result['warnings']:
                if item['check'] == 'quote_excerpt':
                    total['excerpt'] += 1
    total['samples'] = samples
    return total


def find_db(root):
    root = Path(root)
    if root.is_file():
        return root
    candidate = root / 'manual_test_memory.sqlite3'
    if candidate.is_file():
        return candidate
    matches = list(root.rglob('manual_test_memory.sqlite3'))
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError('cannot locate manual_test_memory.sqlite3 under %s' % root)


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读记忆审计（V1）')
    parser.add_argument('--db', help='SQLite 文件')
    parser.add_argument('--root', help='插件数据目录（含 manual_test_memory.sqlite3）')
    parser.add_argument('--scope', help='只审计一个 scope')
    parser.add_argument('--packets', help='packets.jsonl')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args(argv)

    try:
        db_path = find_db(args.db or args.root or '.')
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    database = audit_database(db_path, scope=args.scope)
    result = {'database': database}
    if args.packets:
        result['packets'] = audit_packets(db_path, args.packets)

    errors = len(database['errors'])
    if 'packets' in result:
        errors += result['packets']['errors']
    result['ok'] = errors == 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('DB: %s' % db_path)
        print('  captures=%s errors=%d warnings=%d' % (
            next((x['count'] for x in database['info'] if x['check'] == 'capture_rows'), '?'),
            len(database['errors']), len(database['warnings'])))
        for item in database['errors'][:20]:
            print('  ERROR %s' % json.dumps(item, ensure_ascii=False))
        for item in database['warnings'][:10]:
            print('  WARN  %s' % json.dumps(item, ensure_ascii=False))
        if 'packets' in result:
            p = result['packets']
            print('Packets: %d, quotes=%d, titles=%d, seq_refs=%d, errors=%d' % (
                p['packets'], p['quotes'], p['titles'], p['seq_refs'], p['errors']))
            print('  cross_scope=%d unclassified=%d future=%d seq_missing=%d excerpt=%d' % (
                p['cross_scope'], p['unclassified'], p['future'], p['seq_missing'], p['excerpt']))
            for item in p['samples'][:10]:
                print('  PACKET %s' % json.dumps(item, ensure_ascii=False))
        print('OK' if result['ok'] else 'FAILED')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
