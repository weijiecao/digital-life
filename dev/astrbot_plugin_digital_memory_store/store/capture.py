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

    def judge_candidates(self,scope,query,limit=5):
        """给 LLM 候选筛选器准备有限候选：按字面重合排序，无重合时退回最近记录。"""
        if type(limit) is not int or not 1<=limit<=5:raise ValueError('invalid limit')
        pairs=self._character_pairs(query) if isinstance(query,str) else set()
        rows=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
            WHERE scope=? AND state='pending' ORDER BY seq DESC LIMIT 50''',(scope,)).fetchall()
        scored=[]
        for row in rows:
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:continue
            overlap=sum(1 for pair in pairs if pair in row['content'])
            scored.append((overlap,row['seq'],row))
        scored.sort(key=lambda item:(-item[0],-item[1]))
        chosen=[row for _,_,row in scored[:limit]]
        chosen.sort(key=lambda row:row['seq'])
        return [{'seq':row['seq'],'content':row['content'],'recorded_at':row['recorded_at']}
                for row in chosen]

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
