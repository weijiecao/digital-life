"""Store 的 capture 领域方法（mixin，由 core.Store 组合）。"""
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import hashlib
import sqlite3
import threading
import uuid
import json
import re
import base64
import math
from collections import Counter, OrderedDict
from functools import wraps
from ..engine.e418.engine import UnifiedMemoryEngine, _fingerprint
from ..engine.e419.dynamic_index import attach_dynamic_index
from ..engine.e419.dynamic_index import _unit
from ..engine.e32.cpu_model import CPUSentenceEmbedder
from ..model_drafts import validate_output,validate_contrast,validate_brief_output,make_brief_prompt
from ..model_groups import validate_group_output
from ..new_groups import validate_new_groups
from ..lexical_index import LexicalIndex
from ..pending_index import PendingIndex
from .base import (MAX_CAPTURE_CHARS, _FOLLOWUP_UPDATE, _GENERIC_GRAMS, _RollbackBatch,
                   _UPDATE_CUES, atomic_write)

class CaptureMixin:
    def scope(self, sender, origin):
        data=(str(len(sender))+':'+sender+origin).encode('utf-8')
        return 'manual-test:'+hashlib.sha256(data).hexdigest()

    def record_capture_gap(self,scope,detected_at=None):
        with self.flow.db:
            self.flow.db.execute('''INSERT INTO dl_capture_gaps(scope,unresolved,total,last_at) VALUES(?,1,1,?)
                ON CONFLICT(scope) DO UPDATE SET unresolved=unresolved+1,total=total+1,
                last_at=MAX(last_at,excluded.last_at)''',
                (scope,detected_at or datetime.now(timezone.utc).isoformat(timespec='seconds')))

    def capture_gap_boundary(self,scope):
        row=self.flow.db.execute('SELECT total,last_at FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
        return row['last_at'] if row and row['total'] else None

    def capture_gap_count(self,scope):
        row=self.flow.db.execute('SELECT unresolved FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
        return row['unresolved'] if row else 0

    def acknowledge_capture_gap(self,scope):
        with self.flow.db:
            self.flow.db.execute('UPDATE dl_capture_gaps SET unresolved=0 WHERE scope=?',(scope,))

    def capture(self,scope,message_id,text,*,recorded_at=None):
        if not isinstance(message_id,str) or not 1<=len(message_id)<=256:
            raise ValueError('invalid message id')
        if not isinstance(text,str) or not text.strip() or len(text)>MAX_CAPTURE_CHARS or '\x00' in text:
            raise ValueError('invalid message text')
        if recorded_at is None:
            recorded_at=datetime.now(timezone.utc)
        else:
            recorded_at=datetime.fromisoformat(recorded_at)
            if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:raise ValueError('invalid recorded time')
            recorded_at=recorded_at.astimezone(timezone.utc)
        recorded_at=recorded_at.isoformat(timespec='seconds')
        event_hash=hashlib.sha256((scope+'\0'+message_id).encode('utf-8')).hexdigest()
        digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
        db=self.flow.db
        with db:
            row=db.execute('SELECT content_sha FROM dl_capture WHERE scope=? AND event_hash=?',
                           (scope,event_hash)).fetchone()
            if row:
                if row['content_sha']!=digest:raise ValueError('duplicate id with different content')
                return False
            cursor=db.execute('INSERT INTO dl_capture(scope,event_hash,content,content_sha,recorded_at) VALUES(?,?,?,?,?)',
                (scope,event_hash,text,digest,recorded_at))
            # 与原话同事务落盘；关闭自动整理时也保存任务，稍后可继续。
            db.execute('INSERT INTO dl_auto_jobs(scope,seq) VALUES(?,?)',(scope,cursor.lastrowid))
            if self.auto_model_enabled:
                db.execute('INSERT INTO dl_auto_model_jobs(scope,seq,policy) VALUES(?,?,?)',(scope,cursor.lastrowid,self.auto_model_policy))
        # This is a cheap, rule-based suggestion only; failure never drops the raw record.
        row=db.execute('SELECT seq FROM dl_capture WHERE scope=? AND event_hash=?',(scope,event_hash)).fetchone()
        try:self.propose(scope,row['seq'],semantic=False)
        except Exception:pass
        return True

    def memory_inventory(self,scope):
        """只统计当前私聊作用域，不返回原话或调用模型。"""
        db=self.flow.db
        captures=db.execute('''SELECT COUNT(*) AS total,
            SUM(CASE WHEN c.state='pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN c.state='linked' THEN 1 ELSE 0 END) AS linked,
            SUM(CASE WHEN p.seq IS NOT NULL THEN 1 ELSE 0 END) AS imported
            FROM dl_capture c LEFT JOIN dl_import_provenance p ON p.scope=c.scope AND p.seq=c.seq
            WHERE c.scope=?''',(scope,)).fetchone()
        approved=db.execute('''SELECT COUNT(*) AS records,COUNT(DISTINCT p.event_id) AS events
            FROM e417_phase p JOIN e417_ingest i ON i.scope=p.scope AND i.message_id=p.message_id
            JOIN messages m ON m.scope=i.scope AND m.id=i.message_id
            WHERE p.scope=? AND i.state='approved_link' AND m.speaker='user' ''',(scope,)).fetchone()
        briefs=db.execute('SELECT COUNT(*) FROM dl_source_briefs WHERE scope=?',(scope,)).fetchone()[0]
        return {'captured':captures['total'],'pending':captures['pending'] or 0,
                'linked_captures':captures['linked'] or 0,'imported':captures['imported'] or 0,
                'reviewed_sources':approved['records'],'events':approved['events'],'brief_versions':briefs}

    def auto_job_health(self,scope):
        """统计后台整理任务；dead 表示已停止重试，原话仍保留、仍可人工查看。"""
        rows=self.flow.db.execute('SELECT status,COUNT(*) FROM dl_auto_jobs WHERE scope=? GROUP BY status',(scope,)).fetchall()
        counts={row[0]:row[1] for row in rows}
        return {'pending':counts.get('pending',0),'done':counts.get('done',0),'dead':counts.get('dead',0)}

    def _judge_event_candidates(self,scope,query,pairs,limit=2):
        """事件线：按事件标题/别名与 query 的重合选事件，再取该事件最近+最早来源。"""
        if limit<=0:return []
        db=self.flow.db
        try:
            events=db.execute('''SELECT e.id,e.title,MAX(m.recorded_at) AS last_at
                FROM events e
                LEFT JOIN membership mm ON mm.scope=e.scope AND mm.event_id=e.id
                LEFT JOIN messages m ON m.scope=mm.scope AND m.id=mm.message_id
                    AND m.speaker='user' AND m.kind='user_message'
                WHERE e.scope=?
                GROUP BY e.id,e.title
                HAVING MAX(m.recorded_at) IS NOT NULL
                ORDER BY last_at DESC LIMIT 30''',(scope,)).fetchall()
        except sqlite3.DatabaseError:
            return []
        aliases={}
        try:
            for row in db.execute('SELECT event_id,alias FROM dl_event_alias WHERE scope=?',(scope,)):
                aliases.setdefault(row['event_id'],[]).append(row['alias'] or '')
        except sqlite3.DatabaseError:
            aliases={}
        scored=[]
        for event in events:
            title=event['title'] or ''
            names=[title]+aliases.get(event['id'],[])
            overlap=sum(1 for pair in pairs if any(pair in name for name in names))
            scored.append((overlap,event['last_at'] or '',event))
        scored.sort(key=lambda entry:entry[1],reverse=True)
        scored.sort(key=lambda entry:-entry[0])
        out=[];seen=set()
        for _,_,event in scored:
            if len(out)>=limit:break
            try:
                rows=db.execute('''SELECT m.id,m.content,m.recorded_at
                    FROM messages m JOIN membership mm
                      ON mm.scope=m.scope AND mm.message_id=m.id
                    WHERE mm.scope=? AND mm.event_id=? AND m.speaker='user'
                      AND m.kind='user_message'
                    ORDER BY m.recorded_at DESC LIMIT 2''',(scope,event['id'])).fetchall()
                first=db.execute('''SELECT m.id,m.content,m.recorded_at
                    FROM messages m JOIN membership mm
                      ON mm.scope=m.scope AND mm.message_id=m.id
                    WHERE mm.scope=? AND mm.event_id=? AND m.speaker='user'
                      AND m.kind='user_message'
                    ORDER BY m.recorded_at ASC LIMIT 1''',(scope,event['id'])).fetchall()
            except sqlite3.DatabaseError:
                continue
            for row in list(rows)+list(first):
                content=row['content']
                if not content or content in seen:continue
                seen.add(content)
                out.append({'kind':'approved','line':'event','seq':None,
                            'content':content,'recorded_at':row['recorded_at'],
                            'source_id':row['id'],'event_title':event['title']})
                if len(out)>=limit:break
        return out

    def judge_candidates(self,scope,query,limit=5):
        """给 LLM 候选筛选器准备有限候选：待审原话 + 已审核事件来源，按字面重合排序。"""
        if type(limit) is not int or not 1<=limit<=5:raise ValueError('invalid limit')
        pairs=self._character_pairs(query) if isinstance(query,str) else set()
        candidates=[]
        try:
            rows=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
                WHERE scope=? AND state='pending' ORDER BY seq DESC LIMIT 50''',(scope,)).fetchall()
        except sqlite3.DatabaseError:
            rows=[]
        for row in rows:
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:continue
            candidates.append({'kind':'pending','line':'time','seq':row['seq'],'content':row['content'],
                               'recorded_at':row['recorded_at'],'source_id':None,'event_title':None})
        # 本地语义没命中时，相关来源可能已经在已审核事件里；只读候选，不改变事实。
        try:
            approved=self.flow.db.execute('''SELECT m.id AS source_id,m.content,m.recorded_at,
                    e.id AS event_id,e.title AS event_title
                FROM messages m
                JOIN membership mm ON mm.scope=m.scope AND mm.message_id=m.id
                JOIN events e ON e.scope=mm.scope AND e.id=mm.event_id
                WHERE m.scope=? AND m.speaker='user' AND m.kind='user_message'
                ORDER BY m.recorded_at DESC LIMIT 100''',(scope,)).fetchall()
        except sqlite3.DatabaseError:
            approved=[]
        for row in approved:
            if not row['content']:continue
            candidates.append({'kind':'approved','line':'time','seq':None,'content':row['content'],
                               'recorded_at':row['recorded_at'],'source_id':row['source_id'],
                               'event_title':row['event_title']})
        seen=set();unique=[]
        for item in candidates:
            key=item['content']
            if key in seen:continue
            seen.add(key);unique.append(item)
        scored=[]
        for item in unique:
            overlap=sum(1 for pair in pairs if pair in item['content'])
            scored.append((overlap,item['recorded_at'],item))
        scored.sort(key=lambda entry:entry[1],reverse=True)
        scored.sort(key=lambda entry:-entry[0])
        # 时间线只取最近 N 条；事件线单独取“旧但属于相关事件”的来源，避免只看最近。
        time_quota=min(3,limit)
        time_chosen=[item for _,_,item in scored[:time_quota]]
        time_chosen.sort(key=lambda item:item['recorded_at'])
        event_chosen=self._judge_event_candidates(scope,query,pairs,limit-time_quota)
        selected=[];picked=set()
        for item in time_chosen+event_chosen:
            content=item['content']
            if content in picked:continue
            picked.add(content);selected.append(item)
            if len(selected)>=limit:break
        # 事件线不足时用剩余时间候选补齐；反之亦然。
        for item in [entry[2] for entry in scored]:
            if len(selected)>=limit:break
            if item['content'] in picked:continue
            picked.add(item['content']);selected.append(item)
        return selected[:limit]

    def delete_capture(self,scope,seq,purge=True):
        """删除单条原话及其派生行；purge=True 时 checkpoint + VACUUM 清空自由页。"""
        if type(seq) is not int or seq<1:raise ValueError('invalid sequence')
        db=self.flow.db
        row=db.execute('SELECT seq FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
        if not row:return False
        deps=(('dl_suggestions','capture_seq'),('dl_new_title_suggestions','capture_seq'),
              ('dl_contrast_drafts','capture_seq'),('dl_auto_model_jobs','seq'),
              ('dl_auto_jobs','seq'),('dl_import_provenance','seq'),
              ('dl_auto_localization','seq'),('dl_pending_dirty','seq'))
        with db:
            for table,column in deps:
                try:db.execute('DELETE FROM %s WHERE scope=? AND %s=?'%(table,column),(scope,seq))
                except sqlite3.DatabaseError:pass
            # dl_capture 的 AFTER DELETE 触发器会清理文本/向量/主题派生表。
            db.execute('DELETE FROM dl_capture WHERE scope=? AND seq=?',(scope,seq))
        if purge:self._purge_deleted()
        return True

    def delete_scope(self,scope,purge=True):
        """删除整个 scope 的记忆与派生数据；返回删除的原话条数。"""
        db=self.flow.db
        count=db.execute('SELECT COUNT(*) FROM dl_capture WHERE scope=?',(scope,)).fetchone()[0]
        tables=[r['name'] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            with db:
                for table in tables:
                    try:columns={r['name'] for r in db.execute('PRAGMA table_info(%s)'%table)}
                    except sqlite3.DatabaseError:continue
                    if 'scope' not in columns:continue
                    db.execute('DELETE FROM %s WHERE scope=?'%table,(scope,))
        finally:
            db.execute('PRAGMA foreign_keys=ON')
        if purge:self._purge_deleted()
        return count

    def _purge_deleted(self):
        db=self.flow.db
        try:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.DatabaseError:pass
        try:db.execute('VACUUM')
        except sqlite3.DatabaseError:pass

    def pending(self,scope,after=0):
        if type(after) is not int or after<0:raise ValueError('invalid cursor')
        return [dict(r) for r in self.flow.db.execute('''SELECT c.seq,c.content,c.recorded_at,
                  CASE WHEN p.seq IS NULL THEN 0 ELSE 1 END AS imported
                  FROM dl_capture c LEFT JOIN dl_import_provenance p ON p.scope=c.scope AND p.seq=c.seq
                  WHERE c.scope=? AND c.state='pending' AND c.seq>? ORDER BY c.seq ASC LIMIT 10''',(scope,after))]

    def import_provenance(self,scope,seq):
        if type(seq) is not int or seq<1:raise ValueError('invalid sequence')
        row=self.flow.db.execute('''SELECT p.source_label,p.original_timestamp,p.imported_at,c.recorded_at,c.state
            FROM dl_import_provenance p JOIN dl_capture c ON c.scope=p.scope AND c.seq=p.seq
            WHERE p.scope=? AND p.seq=?''',(scope,seq)).fetchone()
        return dict(row) if row else None
