"""Offline verification and non-overwriting recovery for the isolated test store."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

_APP_ID=0x4C494645
_REQUIRED={
    'messages':{'scope','id','origin','speaker','kind','content','recorded_at'},
    'events':{'scope','id','title'},
    'relations':{'scope','newer','older','kind'},
    'membership':{'scope','event_id','message_id'},
    'summaries':{'scope','id','content','review','validity'},
    'evidence':{'scope','summary_id','message_id'},
    'e417_audit':{'scope','message_id','action','actor','created_at'},
    'e417_ingest':{'scope','origin','message_id','state','source_sha256','candidate_event_id'},
    'e417_phase':{'scope','message_id','event_id','phase'},
    'dl_capture':{'seq','scope','event_hash','content','content_sha','recorded_at','state','linked_source','linked_title'},
}
_OPTIONAL={'dl_import_provenance','dl_source_briefs','dl_brief_model_drafts',
           'dl_brief_segment_results','dl_new_group_drafts','dl_new_group_events',
           'dl_event_alias','dl_event_priority','dl_date_annotations','dl_capture_gaps'}


def _open_readonly(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file():raise ValueError('backup is not a file')
    # `immutable=1`：备份是单文件快照，不需要 WAL；同时避免只读打开时生成
    # `-wal/-shm` 旁文件（否则校验会污染备份目录，轮转可能把旁文件当备份）。
    conn=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=5)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA trusted_schema=OFF')
    return conn


SIDECAR_SUFFIXES=('-wal','-shm','-journal')


def cleanup_sidecars(path):
    """删除指定备份旁的 -wal/-shm/-journal；返回实际删除的文件名。"""
    removed=[]
    for suffix in SIDECAR_SUFFIXES:
        candidate=Path(str(path)+suffix)
        try:
            candidate.unlink()
            removed.append(candidate.name)
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return removed


def verify_connection(conn):
    """校验标识、核心表、物理结构、引用以及已存原文哈希。"""
    report={'ok':False,'checks':{},'counts':{},'errors':[],'warnings':[]}
    if conn.execute('PRAGMA application_id').fetchone()[0]!=_APP_ID:
        report['errors'].append('unknown_application_id');return report
    report['checks']['application_id']=True
    tables={row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing=[]
    for table,columns in _REQUIRED.items():
        if table not in tables:
            missing.append(table);continue
        # 表名只来自固定白名单，不引用用户输入。
        present={row['name'] for row in conn.execute('PRAGMA table_info('+table+')')}
        if not columns<=present:missing.append(table)
    if missing:
        report['errors'].append('missing_or_incompatible_core_tables');return report
    report['checks']['core_schema']=True
    absent=sorted(_OPTIONAL-tables)
    if absent:report['warnings'].append({'missing_optional_tables':absent})
    if [r[0] for r in conn.execute('PRAGMA integrity_check')]!=['ok']:
        report['errors'].append('sqlite_integrity_failed');return report
    report['checks']['sqlite_integrity']=True
    fk_count=sum(1 for _ in conn.execute('PRAGMA foreign_key_check'))
    report['counts']['foreign_key_violations']=fk_count
    report['checks']['foreign_keys']=fk_count==0
    if fk_count:report['errors'].append('foreign_key_violations')
    capture_count=0;capture_mismatch=0
    for row in conn.execute('SELECT content,content_sha FROM dl_capture'):
        capture_count+=1
        try:valid=hashlib.sha256(row['content'].encode('utf-8')).hexdigest()==row['content_sha']
        except (AttributeError,UnicodeError):valid=False
        if not valid:capture_mismatch+=1
    evidence_count=0;evidence_mismatch=0
    for row in conn.execute('''SELECT m.content,i.source_sha256 FROM e417_ingest i
        JOIN messages m ON m.scope=i.scope AND m.id=i.message_id'''):
        evidence_count+=1
        try:valid=hashlib.sha256(row['content'].encode('utf-8')).hexdigest()==row['source_sha256']
        except (AttributeError,UnicodeError):valid=False
        if not valid:evidence_mismatch+=1
    report['counts'].update({'captured':capture_count,'ingested':evidence_count,
        'capture_hash_mismatches':capture_mismatch,'ingest_hash_mismatches':evidence_mismatch})
    report['checks']['capture_hashes']=capture_mismatch==0
    report['checks']['ingest_hashes']=evidence_mismatch==0
    if capture_mismatch:report['errors'].append('capture_hash_mismatch')
    if evidence_mismatch:report['errors'].append('ingest_hash_mismatch')
    # 采集表没有外键：确认后的原话、审核结果和事件成员关系须共同对应。
    broken=conn.execute('''SELECT COUNT(*) FROM dl_capture c
        LEFT JOIN messages m ON m.scope=c.scope AND m.id=c.linked_source
        LEFT JOIN e417_ingest i ON i.scope=c.scope AND i.message_id=c.linked_source
        LEFT JOIN events e ON e.scope=c.scope AND e.id=i.candidate_event_id
        LEFT JOIN membership x ON x.scope=c.scope AND x.event_id=e.id AND x.message_id=m.id
        WHERE c.state='linked' AND (m.id IS NULL OR i.message_id IS NULL OR e.id IS NULL
          OR x.message_id IS NULL OR i.state!='approved_link' OR m.speaker!='user'
          OR m.kind!='user_message' OR m.origin!='captured:'||c.event_hash
          OR i.origin!=m.origin OR m.content!=c.content OR m.recorded_at!=c.recorded_at
          OR e.title IS NOT c.linked_title)''').fetchone()[0]
    report['counts']['broken_capture_links']=broken
    report['checks']['capture_links']=broken==0
    if broken:report['errors'].append('broken_capture_links')
    report['ok']=not report['errors']
    return report


def verify_backup(path):
    conn=_open_readonly(path)
    try:
        # 单个读取事务令全部检查基于一致快照。
        conn.execute('BEGIN')
        return verify_connection(conn)
    finally:conn.close()


def restore_to_new_directory(source,target_dir):
    """恢复到新建目录；已有文件或目录均不覆盖。"""
    source=Path(source).resolve(strict=True);target_dir=Path(target_dir)
    src=_open_readonly(source)
    created_dir=False;target=target_dir/'manual_test_memory.sqlite3'
    try:
        src.execute('BEGIN')
        report=verify_connection(src)
        if not report['ok']:raise ValueError('backup validation failed: '+','.join(report['errors']))
        # 不使用 exist_ok，已有路径（包括符号链接）一律拒绝。
        target_dir.mkdir(mode=0o700,parents=False,exist_ok=False);created_dir=True
        with target.open('xb'):pass
        dst=sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:dst.close()
        copied=verify_backup(target)
        if not copied['ok']:raise ValueError('restored copy validation failed: '+','.join(copied['errors']))
        if copied['counts']!=report['counts']:raise ValueError('restored copy counts changed')
        return {'ok':True,'target':str(target),'checks':copied['checks'],'counts':copied['counts'],'warnings':copied['warnings']}
    except Exception:
        if created_dir:
            # 只清理本次刚创建、尚未交付的输出；不会删除已有目录。
            for suffix in ('','-journal','-wal','-shm'):
                partial=Path(str(target)+suffix)
                if partial.exists():partial.unlink()
            target_dir.rmdir()
        raise
    finally:
        src.close()
        cleanup_sidecars(source)


def main(argv=None):
    parser=argparse.ArgumentParser(description='离线检查和恢复独立测试记忆库')
    sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('verify');check.add_argument('--backup',required=True)
    restore=sub.add_parser('restore');restore.add_argument('--backup',required=True)
    restore.add_argument('--target-dir',required=True)
    args=parser.parse_args(argv)
    try:
        result=verify_backup(args.backup) if args.command=='verify' else restore_to_new_directory(args.backup,args.target_dir)
        if args.command=='verify':cleanup_sidecars(args.backup)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 2
    except (ValueError,OSError,sqlite3.Error) as exc:
        print('操作未完成：'+str(exc),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
