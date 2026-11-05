"""Store 的 relations 领域方法（mixin，由 core.Store 组合）。"""
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

class RelationMixin:
    @atomic_write
    def link_capture_relation(self,scope,seq,title,old_id,kind):
        """Explicitly attach a captured line and relate it to one reviewed source."""
        if type(seq) is not int or not 1<=seq<=9223372036854775807:
            raise ValueError('invalid sequence')
        if kind not in ('corrects','supersedes','related') or not isinstance(old_id,str) or not old_id:
            raise ValueError('invalid relation')
        db=self.flow.db
        capture=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
        if not capture or capture['state'] not in ('pending','linked'):
            raise ValueError('capture unavailable')
        if hashlib.sha256(capture['content'].encode('utf-8')).hexdigest()!=capture['content_sha']:
            raise ValueError('cannot preserve source')
        if capture['state']=='linked' and capture['linked_title']!=title:
            raise ValueError('capture linked elsewhere')
        event=self._event_by_title(scope,title)
        snap=self.flow._snapshot(scope,event['id'])
        old=next((r for r in snap['rows'] if r['id']==old_id),None)
        if not old:raise ValueError('old source missing')
        new_time=datetime.fromisoformat(capture['recorded_at']);old_time=datetime.fromisoformat(old['recorded_at'])
        if new_time.utcoffset() is None or old_time.utcoffset() is None or old_time>=new_time:
            raise ValueError('source chronology unknown')
        prior=self.flow.writer.status(scope,'captured:'+capture['event_hash'])
        linked_id=None
        if prior and prior['state']=='approved_link':
            if prior['candidate_event_id']!=event['id']:raise ValueError('event mismatch')
            linked_id=prior['message_id']
        if capture['state']=='linked' and linked_id!=capture['linked_source']:
            raise ValueError('linked source mismatch')
        if linked_id and db.execute('SELECT 1 FROM relations WHERE scope=? AND newer=? AND older=? AND kind<>?',
                                    (scope,linked_id,old_id,kind)).fetchone():
            raise ValueError('different relation already exists')
        linked_id=self.link_capture(scope,seq,title)
        self.relate_sources(scope,linked_id,old_id,kind)
        return linked_id

    @atomic_write
    def link_capture_batch(self,scope,seqs,title):
        """Validate every selected source before linking; report partial writes for safe retry."""
        if (not isinstance(seqs,list) or not 1<=len(seqs)<=10 or
            any(type(x) is not int or not 1<=x<=9223372036854775807 for x in seqs) or
            len(set(seqs))!=len(seqs)):
            raise ValueError('invalid selection')
        if not isinstance(title,str) or not 1<=len(title)<=80 or any(c in title for c in '\n\r【】'):
            raise ValueError('invalid event')
        seqs=sorted(seqs)
        db=self.flow.db
        for seq in seqs:
            row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
            if not row or row['state'] not in ('pending','linked'):
                raise ValueError('source unavailable')
            if row['state']=='linked' and row['linked_title']!=title:
                raise ValueError('source already linked elsewhere')
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                raise ValueError('source cannot be preserved')
        completed=[]
        for seq in seqs:
            try:self.link_capture(scope,seq,title)
            except Exception:
                return {'completed':completed,'failed':seq,'remaining':[x for x in seqs if x>seq]}
            completed.append(seq)
        return {'completed':completed,'failed':None,'remaining':[]}

    @atomic_write
    def link_capture(self,scope,seq,title):
        if type(seq) is not int or seq<1 or not 1<=len(title)<=80 or any(c in title for c in '\n\r【】'):
            raise ValueError('invalid event or capture id')
        db=self.flow.db
        row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
        if not row:raise ValueError('capture unavailable')
        if row['state']=='linked':
            if row['linked_title']!=title:raise ValueError('already linked to another event')
            return row['linked_source']
        if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
            raise ValueError('capture integrity failed')
        source='captured:'+row['event_hash']
        prior=self.flow.writer.status(scope,source)
        if prior and prior['state']=='approved_link':
            event=db.execute('SELECT title FROM events WHERE scope=? AND id=?',
                             (scope,prior['candidate_event_id'])).fetchone()
            if not event or event['title']!=title:raise ValueError('link recovery mismatch')
            message_id=prior['message_id']
        else:
            matches=self.flow.writer._event_by_title(scope,title)
            if len(matches)>1:raise ValueError('ambiguous title')
            event_id=matches[0]['id'] if matches else self.flow.core.create_event(scope,title)
            result=self.flow.ingest(scope=scope,origin=source,speaker='user',kind='user_message',
                                    content=row['content'],recorded_at=row['recorded_at'])
            self.flow.review(scope=scope,origin=source,decision='approve',reviewer='explicit-user-command',
                             event_id=event_id,phase='unknown',reason='explicit user event link')
            message_id=result['message_id']
        with self.flow.core.write_scope():
            db.execute("UPDATE dl_capture SET state='linked',linked_source=?,linked_title=? WHERE scope=? AND seq=?",
                       (message_id,title,scope,seq))
            db.execute('DELETE FROM dl_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
            db.execute('DELETE FROM dl_new_title_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
        return message_id

    def annotate_date(self,scope,message_id,value):
        """Append the operator's calendar-date assertion; do not edit source."""
        source=self._approved_source(scope,message_id)
        if value=='未知':value=None
        else:
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('invalid date')
            try:
                if date.fromisoformat(value).isoformat()!=value:raise ValueError()
            except ValueError:raise ValueError('invalid date') from None
        prior=self.flow.db.execute('SELECT occurred_date,source_sha FROM dl_date_annotations WHERE scope=? AND message_id=? ORDER BY id DESC LIMIT 1',(scope,message_id)).fetchone()
        if prior and prior['occurred_date']==value and prior['source_sha']==source['source_sha256']:return False
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_date_annotations(scope,message_id,occurred_date,source_sha,annotated_at) VALUES(?,?,?,?,?)',
                (scope,message_id,value,source['source_sha256'],datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return True

    def date_history(self,scope,message_id,limit=10):
        source=self._approved_source(scope,message_id)
        return [{'occurred_date':r['occurred_date'],'annotated_at':r['annotated_at'],
                 'source_matches':r['source_sha']==source['source_sha256']} for r in self.flow.db.execute('''SELECT occurred_date,annotated_at,source_sha FROM dl_date_annotations
             WHERE scope=? AND message_id=? ORDER BY id DESC LIMIT ?''',(scope,message_id,limit))]

    def timeline(self,scope,mode='记录',cursor='',limit=10):
        """Page reviewed user sources with a stable key; never infer occurred time."""
        if mode not in ('记录','发生') or type(limit) is not int or not 1<=limit<=10:
            raise ValueError('invalid timeline mode')
        occurred="CASE WHEN a.id IS NOT NULL THEN CASE WHEN a.source_sha=i.source_sha256 THEN a.occurred_date ELSE NULL END ELSE substr(m.occurred_at,1,10) END"
        column='m.recorded_at' if mode=='记录' else occurred
        params=[scope];where=''
        if cursor:
            if not isinstance(cursor,str) or len(cursor)>350:raise ValueError('invalid cursor')
            try:
                raw=base64.urlsafe_b64decode(cursor+'='*((-len(cursor))%4))
                key=json.loads(raw.decode('utf-8'))
                if (not isinstance(key,list) or len(key)!=3 or key[0]!=mode or
                    not all(isinstance(x,str) and 1<=len(x)<=100 for x in key[1:]) or
                    (mode=='发生' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',key[1]))):raise ValueError()
            except Exception:raise ValueError('invalid cursor') from None
            where=f' AND ({column}<? OR ({column}=? AND m.id<?))'
            params.extend([key[1],key[1],key[2]])
        params.append(limit+1)
        rows=self.flow.db.execute(f'''SELECT m.id,m.content,m.recorded_at,{occurred} AS occurred_at,
             i.source_sha256,e.title,{column} AS sort_time
             FROM messages m JOIN e417_ingest i ON m.scope=i.scope AND m.id=i.message_id
             JOIN e417_phase p ON p.scope=m.scope AND p.message_id=m.id
             JOIN events e ON e.scope=p.scope AND e.id=p.event_id
             LEFT JOIN dl_date_annotations a ON a.id=(SELECT max(d.id) FROM dl_date_annotations d
                 WHERE d.scope=m.scope AND d.message_id=m.id)
             WHERE m.scope=? AND m.speaker='user' AND i.state='approved_link'
               AND {column} IS NOT NULL {where}
             ORDER BY {column} DESC,m.id DESC LIMIT ?''',params).fetchall()
        page=rows[:limit];result=[]
        for row in page:
            valid=hashlib.sha256(row['content'].encode('utf-8')).hexdigest()==row['source_sha256']
            edges=[dict(r) for r in self.flow.db.execute('''SELECT newer,older,kind FROM relations
                WHERE scope=? AND (newer=? OR older=?) ORDER BY newer,older,kind''',(scope,row['id'],row['id']))]
            result.append({'id':row['id'],'title':row['title'],'content':row['content'] if valid else None,
                           'recorded_at':row['recorded_at'],'occurred_at':row['occurred_at'],
                           'integrity':valid,'relations':edges})
        next_cursor=''
        if len(rows)>limit and page:
            key=[mode,page[-1]['sort_time'],page[-1]['id']]
            next_cursor=base64.urlsafe_b64encode(json.dumps(key,ensure_ascii=False,separators=(',',':')).encode()).decode().rstrip('=')
        return {'records':result,'next_cursor':next_cursor,'mode':mode}

    def relate_sources(self,scope,newer_id,older_id,kind):
        """Link two separately reviewed records; neither record is overwritten."""
        if kind not in ('corrects','supersedes','related'):raise ValueError('invalid relation')
        if not isinstance(newer_id,str) or not isinstance(older_id,str) or newer_id==older_id:
            raise ValueError('invalid source ids')
        db=self.flow.db
        def source(mid):
            return db.execute('''SELECT i.origin,i.state,i.candidate_event_id,m.recorded_at
                FROM e417_ingest i JOIN messages m ON m.scope=i.scope AND m.id=i.message_id
                WHERE i.scope=? AND i.message_id=?''',(scope,mid)).fetchone()
        newer,older=source(newer_id),source(older_id)
        if not newer or not older or newer['state']!='approved_link' or older['state']!='approved_link':
            raise ValueError('source missing or not approved')
        if newer['candidate_event_id']!=older['candidate_event_id']:
            raise ValueError('cross-event relation not supported')
        if db.execute('SELECT 1 FROM relations WHERE scope=? AND newer=? AND older=? AND kind=?',
                      (scope,newer_id,older_id,kind)).fetchone():return False
        self.flow.relate(scope=scope,newer_origin=newer['origin'],older_origin=older['origin'],kind=kind,
                         reviewer='explicit-user-command',reason='user explicitly linked existing records')
        return True

    def create_extract_draft(self,scope,title):
        """Store verbatim excerpts, versioned by the event's source fingerprint."""
        found=self.flow.writer._event_by_title(scope,title)
        if len(found)!=1:raise ValueError('event not uniquely found')
        event_id=found[0]['id'];snap=self.flow._snapshot(scope,event_id)
        if not snap or snap['stale']:raise ValueError('stale or missing source')
        pending=self.flow.db.execute("SELECT count(*) FROM e417_ingest WHERE scope=? AND candidate_event_id=? AND state='pending_review'",(scope,event_id)).fetchone()[0]
        if pending:raise ValueError('pending review on event')
        rows=snap['rows'];chosen=rows[-5:]
        records=[{'id':r['id'],'recorded_at':r['recorded_at'],'content':r['content'],
                  'sha256':hashlib.sha256(r['content'].encode('utf-8')).hexdigest()} for r in chosen]
        draft='D-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_extract_drafts VALUES(?,?,?,?,?,?,?)',
                (scope,draft,event_id,snap['digest'],json.dumps(records,ensure_ascii=False),len(rows),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_extract_draft(scope,draft)

    def read_extract_draft(self,scope,draft_id):
        db=self.flow.db
        row=db.execute('SELECT * FROM dl_extract_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        snap=self.flow._snapshot(scope,row['event_id'])
        state='CURRENT' if snap and not snap['stale'] and snap['digest']==row['event_fingerprint'] else 'STALE'
        pending=db.execute("SELECT count(*) FROM e417_ingest WHERE scope=? AND candidate_event_id=? AND state='pending_review'",(scope,row['event_id'])).fetchone()[0]
        if pending:state='PENDING_UPDATE'
        records=json.loads(row['records_json'])
        if any(hashlib.sha256(x['content'].encode('utf-8')).hexdigest()!=x['sha256'] for x in records):state='STALE'
        return {'draft_id':draft_id,'status':state,'created_at':row['created_at'],
                'records':records,'source_total':row['source_total'],
                'partial':len(records)<row['source_total'],'kind':'VERBATIM_EXTRACT_NOT_INFERENCE'}

    def _unresolved_linked_updates(self,scope,snap,relations):
        """提示含变更词但尚无修正类关系的已关联原话，不判定真实语义。"""
        rows=snap['rows']
        if len(rows)>1000:raise ValueError('event too large')
        resolved={edge['newer'] for edge in relations if edge['kind'] in ('corrects','supersedes','retracts')}
        dismissed={r['message_id']:(r['source_sha'],r['recorded_at']) for r in self.flow.db.execute(
            'SELECT message_id,source_sha,recorded_at FROM dl_update_dismissals WHERE scope=? AND event_id=?',
            (scope,snap['id']))}
        result=[];older=None;previous_time=None;previous=None
        for row in rows:
            stamp=datetime.fromisoformat(row['recorded_at'])
            if stamp.utcoffset() is None:raise ValueError('invalid time')
            if previous_time is not None and stamp>previous_time:older=previous
            if (older and _UPDATE_CUES.search(row['content']) and row['id'] not in resolved and
                dismissed.get(row['id'])!=(row['source_sha256'],row['recorded_at'])):
                result.append({'new_id':row['id'],'content':row['content'],'recorded_at':row['recorded_at'],
                               'old_id':older['id'],'old_content':older['content']})
            previous=row;previous_time=stamp
        return result

    def linked_update_clues(self,scope,title,page=1):
        if type(page) is not int or not 1<=page<=10000:raise ValueError('invalid page')
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        relations=self.flow._event_rows(scope,event['id'])[1]
        clues=self._unresolved_linked_updates(scope,snap,relations)
        start=(page-1)*10
        return {'items':clues[start:start+10],'has_more':len(clues)>start+10}

    def dismiss_linked_update(self,scope,title,message_id):
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        relations=self.flow._event_rows(scope,event['id'])[1]
        if not any(x['new_id']==message_id for x in self._unresolved_linked_updates(scope,snap,relations)):
            raise ValueError('unresolved clue not found')
        row=next(r for r in snap['rows'] if r['id']==message_id)
        with self.flow.db:
            self.flow.db.execute('''INSERT OR REPLACE INTO dl_update_dismissals
              (scope,message_id,event_id,source_sha,recorded_at,reviewed_at) VALUES(?,?,?,?,?,?)''',
              (scope,message_id,event['id'],row['source_sha256'],row['recorded_at'],
               datetime.now(timezone.utc).isoformat(timespec='seconds')))

    def _event_date_token(self,scope,event_id):
        """摘要同时绑定相关来源的最新人工日期标注。"""
        rows=[dict(r) for r in self.flow.db.execute('''SELECT a.message_id,a.id,a.occurred_date,a.source_sha,a.annotated_at
            FROM dl_date_annotations a JOIN e417_phase p ON p.scope=a.scope AND p.message_id=a.message_id
            WHERE a.scope=? AND p.event_id=? AND a.id=(SELECT max(d.id) FROM dl_date_annotations d
            WHERE d.scope=a.scope AND d.message_id=a.message_id) ORDER BY a.message_id''',(scope,event_id))]
        return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
