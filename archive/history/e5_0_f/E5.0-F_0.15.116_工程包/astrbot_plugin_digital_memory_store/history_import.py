"""Offline JSONL import into a test-only independent memory store."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import sqlite3

_MAX_BYTES=10*1024*1024
_MAX_RECORDS=10000


def _unique_keys(pairs):
    data={}
    for key,value in pairs:
        if key in data:raise ValueError('duplicate JSON key')
        data[key]=value
    return data


def parse_jsonl(path):
    """严格验证全部行，绝不静默截断或将非用户消息导入为用户原话。"""
    path=Path(path)
    with path.open('rb') as f:
        data=f.read(_MAX_BYTES+1)
    if len(data)>_MAX_BYTES:raise ValueError('file exceeds 10 MiB')
    text=data.decode('utf-8-sig');records=[];seen=set();now=datetime.now(timezone.utc)
    for line_no,line in enumerate(text.splitlines(),1):
        if not line.strip():continue
        try:
            item=json.loads(line,object_pairs_hook=_unique_keys)
            if not isinstance(item,dict) or set(item)!={'message_id','speaker','content','recorded_at'}:
                raise ValueError('invalid fields')
            mid=item['message_id'];content=item['content'];stamp=item['recorded_at']
            if not isinstance(mid,str) or not 1<=len(mid)<=256 or '\x00' in mid:raise ValueError('invalid message_id')
            if mid in seen:raise ValueError('duplicate message_id in file')
            if item['speaker']!='user':raise ValueError('speaker must be user')
            if not isinstance(content,str) or not content.strip() or len(content)>400 or '\x00' in content:
                raise ValueError('invalid content')
            if not isinstance(stamp,str) or len(stamp)>80:raise ValueError('invalid recorded_at')
            time=datetime.fromisoformat(stamp)
            if time.utcoffset() is None or time>now:raise ValueError('recorded_at needs timezone and cannot be in future')
            normalized=time.astimezone(timezone.utc).isoformat(timespec='seconds')
            # 检查 UTF-8 编码，拒绝无法可靠保存的代理项。
            mid.encode('utf-8');content.encode('utf-8')
            records.append({'message_id':mid,'content':content,'recorded_at':normalized,'original_timestamp':stamp})
            seen.add(mid)
            if len(records)>_MAX_RECORDS:raise ValueError('more than 10000 records')
        except (ValueError,TypeError,OverflowError,UnicodeError) as exc:
            raise ValueError('line '+str(line_no)+': '+str(exc)) from exc
    if not records:raise ValueError('no records')
    # 同秒记录维持文件顺序；平台导出未必保留所有消息。
    records.sort(key=lambda x:x['recorded_at'])
    return records


def import_records(store,scope,records,source_label):
    """整批在单个事务中校验、写入；仅导入为待审消息。"""
    if not isinstance(source_label,str) or not source_label.strip() or len(source_label)>80 or '\x00' in source_label:
        raise ValueError('invalid source label')
    db=store.flow.db;inserted=0;duplicates=0
    db.execute('BEGIN IMMEDIATE')
    try:
        pending=[]
        for item in records:
            event_hash=hashlib.sha256((scope+'\0'+item['message_id']).encode('utf-8')).hexdigest()
            digest=hashlib.sha256(item['content'].encode('utf-8')).hexdigest()
            old=db.execute('SELECT content,content_sha,recorded_at FROM dl_capture WHERE scope=? AND event_hash=?',
                           (scope,event_hash)).fetchone()
            if old:
                if old['content']!=item['content'] or old['content_sha']!=digest or old['recorded_at']!=item['recorded_at']:
                    raise ValueError('message_id collision or changed timestamp')
                duplicates+=1
            else:pending.append((item,event_hash,digest))
        if pending:
            nonimport=db.execute('''SELECT 1 FROM dl_capture c LEFT JOIN dl_import_provenance p
                ON p.scope=c.scope AND p.seq=c.seq WHERE c.scope=? AND p.seq IS NULL LIMIT 1''',(scope,)).fetchone()
            if nonimport:raise ValueError('scope already contains non-imported captures')
            latest=db.execute('SELECT max(recorded_at) FROM dl_capture WHERE scope=?',(scope,)).fetchone()[0]
            if latest and pending[0][0]['recorded_at']<latest:
                raise ValueError('new import precedes existing imported records')
        imported_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
        for item,event_hash,digest in pending:
            cursor=db.execute('''INSERT INTO dl_capture(scope,event_hash,content,content_sha,recorded_at)
                VALUES(?,?,?,?,?)''',(scope,event_hash,item['content'],digest,item['recorded_at']))
            db.execute('INSERT INTO dl_import_provenance VALUES(?,?,?,?,?)',
                       (scope,cursor.lastrowid,source_label,item['original_timestamp'],imported_at))
            inserted+=1
        db.commit()
    except Exception:
        db.rollback();raise
    return {'inserted':inserted,'duplicates':duplicates,'state':'pending'}


def main(argv=None):
    parser=argparse.ArgumentParser(description='独立测试记忆库：验证或导入用户原话 JSONL')
    parser.add_argument('--file',required=True,help='UTF-8 JSONL 文件')
    parser.add_argument('--data-dir',required=True,help='独立测试记忆数据库目录')
    parser.add_argument('--sender',required=True,help='与目标私聊相同的发送者 ID')
    parser.add_argument('--origin',required=True,help='与目标私聊完全相同的 unified_msg_origin')
    parser.add_argument('--source-label',required=True,help='导出来源的非敏感标签')
    parser.add_argument('--apply',action='store_true',help='实际写入；不加此项只验证文件格式')
    args=parser.parse_args(argv)
    try:
        if not 1<=len(args.sender)<=128 or not 1<=len(args.origin)<=512 or ':friendmessage:' not in args.origin.casefold():
            raise ValueError('sender or private-message origin invalid')
        if not args.source_label.strip() or len(args.source_label)>80 or '\x00' in args.source_label:
            raise ValueError('invalid source label')
        data_dir=Path(args.data_dir)
        if not (data_dir/'manual_test_memory.sqlite3').is_file():raise ValueError('test database not found')
        records=parse_jsonl(args.file)
        result={'mode':'apply' if args.apply else 'preview','valid_records':len(records),
                'first_recorded_at':records[0]['recorded_at'],'last_recorded_at':records[-1]['recorded_at']}
        if args.apply:
            from .storage import Store
            store=Store(data_dir,'')
            try:result.update(import_records(store,store.scope(args.sender,args.origin),records,args.source_label))
            finally:store.close()
        else:result['notice']='preview validates file only; database collisions and ordering are checked during apply'
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,TypeError,UnicodeError,OSError,sqlite3.Error) as exc:
        print('导入失败：'+str(exc),file=sys.stderr);return 2


if __name__=='__main__':
    raise SystemExit(main())
