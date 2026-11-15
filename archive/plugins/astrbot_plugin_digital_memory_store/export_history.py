"""导出当前会话的原话：JSONL（可回灌导入）+ Markdown（人读）+ 元数据。

设计边界：
- JSONL 与 `history_import.parse_jsonl` 完全兼容，每行只有四个字段，可直接 `/dlm 导入历史` 回灌；
- 超过 400 字的消息**不写进 JSONL**（导入端会拒绝），只写进 Markdown，并在元数据里记条数；
- 消息 ID 是合成的 `exp-<作用域指纹>-<seq>`：同一导出文件重复导入会被去重，但不等于平台原始 ID；
- 只导出原话，不导出派生索引/事件/草稿——那些可以在新机器上重建。
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 与 history_import.parse_jsonl 的 content 上限保持一致
IMPORT_MAX_CHARS = 400


def _fingerprint(scope):
    return hashlib.sha256(str(scope).encode('utf-8')).hexdigest()[:8]


def _plugin_version():
    try:
        text = (Path(__file__).with_name('metadata.yaml')).read_text(encoding='utf-8')
    except OSError:
        return ''
    for line in text.splitlines():
        if line.startswith('version:'):
            return line.split(':', 1)[1].strip()
    return ''


def _rows(store, scope, since):
    sql = ('SELECT seq,content,content_sha,recorded_at FROM dl_capture WHERE scope=?')
    args = [scope]
    if since:
        sql += ' AND recorded_at>=?'
        args.append(since)
    sql += ' ORDER BY recorded_at,seq'
    return store.flow.db.execute(sql, args).fetchall()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_scope(store, scope, *, days=0, now=None, out_dir=None):
    """把当前作用域的原话导出到 `<root>/exports/`，返回生成的文件与统计。"""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=int(days))).isoformat(timespec='seconds') if days else ''
    rows = _rows(store, scope, since)
    if not rows:
        return {'count': 0, 'skipped': 0, 'jsonl': '', 'md': '', 'meta': ''}

    folder = Path(out_dir or Path(store.root) / 'exports')
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / ('export-%s-%s' % (_fingerprint(scope), now.strftime('%Y%m%d-%H%M%S')))
    jsonl_path = base.with_suffix('.jsonl')
    md_path = base.with_suffix('.md')
    meta_path = base.with_suffix('.meta.json')

    exported = skipped = 0
    skipped_rows = []
    with jsonl_path.open('w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            content = row['content']
            if len(content) > IMPORT_MAX_CHARS:
                skipped += 1
                skipped_rows.append({'seq': row['seq'], 'chars': len(content),
                                     'recorded_at': row['recorded_at']})
                continue
            handle.write(json.dumps({'message_id': 'exp-%s-%d' % (_fingerprint(scope), row['seq']),
                                     'speaker': 'user', 'content': content,
                                     'recorded_at': row['recorded_at']}, ensure_ascii=False) + '\n')
            exported += 1

    with md_path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write('# 记忆导出（会话指纹 %s）\n\n' % _fingerprint(scope))
        handle.write('- 导出时间：%s\n' % now.isoformat(timespec='seconds'))
        handle.write('- 条数：%d（其中 %d 条超过 %d 字，只在本文件里）\n'
                     % (len(rows), skipped, IMPORT_MAX_CHARS))
        handle.write('- 时间范围：%s ~ %s\n' % (rows[0]['recorded_at'], rows[-1]['recorded_at']))
        handle.write('- 说明：这是原话导出，不含已整理的事件与关系；JSONL 同名文件可直接回灌。\n\n')
        for row in rows:
            handle.write('### #%d %s\n\n```text\n%s\n```\n\n' % (row['seq'], row['recorded_at'], row['content']))

    meta = {'scope_fingerprint': _fingerprint(scope), 'exported_at': now.isoformat(timespec='seconds'),
            'days': int(days), 'plugin_version': _plugin_version(),
            'rows': len(rows), 'jsonl_records': exported, 'skipped_long': skipped,
            'skipped': skipped_rows, 'first': rows[0]['recorded_at'], 'last': rows[-1]['recorded_at'],
            'jsonl_sha256': _sha256(jsonl_path), 'md_sha256': _sha256(md_path),
            'import_hint': 'JSONL 每行四字段，可直接 /dlm 导入历史 回灌；消息 ID 为合成值'}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    return {'count': exported, 'rows': len(rows), 'skipped': skipped,
            'jsonl': str(jsonl_path), 'md': str(md_path), 'meta': str(meta_path),
            'first': rows[0]['recorded_at'], 'last': rows[-1]['recorded_at']}
