"""V2：删除/遗忘能力核验（当前无用户接口）。

不做产品实现，只回答三个问题：
1. 生产代码里是否存在删除入口？
2. 如果直接 `DELETE FROM dl_capture`，现有触发器能清掉多少派生数据？会不会留孤儿？
3. 删除后原话是否还留在主库/WAL（逻辑删除 ≠ 物理擦除）？

产物：reports/verification_v2_delete_absence.json
用法：python verification/v2_delete_absence.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

from astrbot_plugin_digital_memory_store.storage import Store  # noqa: E402
from astrbot_plugin_digital_memory_store.auto_organize import process_jobs  # noqa: E402
from astrbot_plugin_digital_memory_store.auto_cross_update import sync_text_index  # noqa: E402

sys.path.insert(0, str(C.DEV / 'scripts'))
import memory_audit as audit  # noqa: E402

MARKER = '删除残留标记-7f3d-唯一串'
TABLES = ('dl_auto_jobs', 'dl_auto_groups', 'dl_auto_chains', 'dl_auto_localization',
          'dl_auto_source_vectors', 'dl_auto_text_index', 'dl_auto_topic_members',
          'dl_pending_index', 'dl_pending_terms', 'dl_auto_vector_chunks')


class Enc:
    model_key = 'v2-fixture'

    def __init__(self, path):
        pass

    def encode_documents(self, texts):
        return [[1.0, 0.0] if '书' in text else [0.0, 1.0] for text in texts]

    def encode_queries(self, texts):
        return [[1.0, 0.0] if '书' in text else [0.0, 1.0] for text in texts]


def counts(db):
    result = {}
    for table in TABLES:
        try:
            result[table] = db.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
        except sqlite3.DatabaseError:
            result[table] = None
    return result


def contains_marker(path, marker=MARKER):
    data = Path(path).read_bytes()
    return marker.encode('utf-8') in data


def production_delete_paths():
    root = C.DEV / 'astrbot_plugin_digital_memory_store'
    hits = []
    for path in root.rglob('*.py'):
        if '__pycache__' in str(path):
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        if 'DELETE FROM dl_capture' in text:
            hits.append(str(path.relative_to(root)))
    command_hits = []
    for name in ('commands.py', 'main.py'):
        text = (root / name).read_text(encoding='utf-8', errors='replace')
        if '/dlm 删除' in text or '删除会话' in text:
            command_hits.append(name)
    return {'sql_delete_files': hits, 'command_files': command_hits}


def main():
    paths = production_delete_paths()
    with tempfile.TemporaryDirectory(prefix='dl-v2-') as root:
        root = Path(root)
        store = Store(root, '', Enc)
        db = store.flow.db
        scope = store.scope('42', 'test:FriendMessage:42')
        for index in range(6):
            store.capture(scope, 'm-%d' % index,
                          ('删除测试第%d条：周末去书店找资料，%s' % (index, MARKER))
                          if index == 3 else '删除测试第%d条：周末去书店找资料' % index)
        process_jobs(db)
        sync_text_index(db, limit=100)
        store.sync_auto_source_vectors(limit=20)
        before = counts(db)
        rows_before = db.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
        target = db.execute(
            'SELECT seq FROM dl_capture WHERE content LIKE ?', ('%' + MARKER + '%',)).fetchone()
        target_seq = target[0]
        store.delete_capture(scope, target_seq, purge=True)
        after = counts(db)
        rows_after = db.execute('SELECT COUNT(*) FROM dl_capture').fetchone()[0]
        db.close()

        db_file = root / 'manual_test_memory.sqlite3'
        wal_file = Path(str(db_file) + '-wal')
        raw_before = contains_marker(db_file)
        wal_before = wal_file.is_file() and contains_marker(wal_file)

        audit_result = audit.audit_database(db_file)

        # 物理擦除对照：checkpoint + VACUUM 后再看。
        conn = sqlite3.connect(str(db_file))
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('VACUUM')
            conn.commit()
        finally:
            conn.close()
        raw_after = contains_marker(db_file)
        wal_after = wal_file.is_file() and contains_marker(wal_file)

    residue = {table: before[table] - after[table]
               for table in TABLES if before[table] is not None}
    payload = {
        'suite': 'v2-delete-absence',
        'production_paths': paths,
        'has_user_delete': bool(paths['sql_delete_files'] or paths['command_files']),
        'rows': {'before': rows_before, 'after': rows_after, 'removed': 1},
        'derived_before': before,
        'derived_after': after,
        'derived_removed': residue,
        'audit_after_delete': {'errors': audit_result['errors'],
                               'warnings': audit_result['warnings']},
        'raw_marker_before_vacuum': {'db': raw_before, 'wal': wal_before},
        'raw_marker_after_vacuum': {'db': raw_after, 'wal': wal_after},
        'checks': {
            'has_user_delete': payload_has_delete(paths),
            'capture_deleted': rows_after == rows_before - 1,
            'no_orphans': not audit_result['errors'],
            'marker_gone_before_vacuum': not raw_before and not wal_before,
        },
    }
    payload['pass'] = all(payload['checks'].values())
    out = C.DEV / 'reports' / 'verification_v2_delete_absence.json'
    C.dump_json(out, payload)
    print(json.dumps({'has_user_delete': payload['has_user_delete'],
                      'derived_removed': residue,
                      'audit_errors': audit_result['errors'][:5],
                      'marker_before_vacuum': payload['raw_marker_before_vacuum'],
                      'marker_after_vacuum': payload['raw_marker_after_vacuum'],
                      'report': str(out)}, ensure_ascii=False, indent=2))
    return 0


def payload_has_delete(paths):
    return bool(paths['sql_delete_files'] or paths['command_files'])


if __name__ == '__main__':
    raise SystemExit(main())
