"""Store 的 briefs 领域方法（mixin，由 core.Store 组合）。"""
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

class BriefMixin:
    def model_brief_snapshot(self,scope,title,as_of=None):
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        if not snap or snap['stale'] or not 1<=len(snap['rows'])<=20:raise ValueError('invalid source count')
        cutoff=datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
        if cutoff.utcoffset() is None:raise ValueError('invalid cutoff')
        for row in snap['rows']:
            t=datetime.fromisoformat(row['recorded_at'])
            if t.utcoffset() is None or t>=cutoff:raise ValueError('source time')
        relations=self.flow._event_rows(scope,event['id'])[1]
        if self._pending_update_hint(scope,title,snapshot=snap) or self._unresolved_linked_updates(scope,snap,relations):
            raise ValueError('pending update')
        dates=self._event_annotated_dates(scope,snap,cutoff)
        # 不截断原话或关系：超过预算直接拒绝，避免把局部冒充完整事件。
        data={'title':title,'sources':[r['content'] for r in snap['rows']],
              'relations':relations,'dates':dates}
        if len(relations)>40 or len(json.dumps(data,ensure_ascii=False))>12000:
            raise ValueError('event too large')
        return {'title':title,'event_id':event['id'],'fingerprint':snap['digest'],
                'date_token':self._event_date_token(scope,event['id']),'as_of':cutoff.isoformat(),
                'sources':[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at'],
                            'sha':r['source_sha256']} for r in snap['rows']],
                'relations':relations,'dates':dates}

    def model_segment_snapshot(self,scope,title,as_of=None):
        """完整关系连通组不拆分，形成有界分段；任何内容超限都拒绝。"""
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        if not snap or snap['stale'] or not 1<=len(snap['rows'])<=100:raise ValueError('source count')
        cutoff=datetime.fromisoformat(as_of) if as_of else datetime.now(timezone.utc)
        if cutoff.utcoffset() is None:raise ValueError('time')
        for r in snap['rows']:
            t=datetime.fromisoformat(r['recorded_at'])
            if t.utcoffset() is None or t>=cutoff:raise ValueError('source time')
        relations=self.flow._event_rows(scope,event['id'])[1]
        if len(relations)>200:raise ValueError('too many relationships')
        if self._pending_update_hint(scope,title,snapshot=snap) or self._unresolved_linked_updates(scope,snap,relations):
            raise ValueError('pending update')
        dates=self._event_annotated_dates(scope,snap,cutoff)
        sources=[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at'],
                  'sha':r['source_sha256']} for r in snap['rows']]
        ids=[r['id'] for r in sources];parent={x:x for x in ids}
        def find(x):
            while parent[x]!=x:
                parent[x]=parent[parent[x]];x=parent[x]
            return x
        for edge in relations:
            a,b=edge['newer'],edge['older']
            if a not in parent or b not in parent:raise ValueError('invalid relationship')
            parent[find(a)]=find(b)
        groups={}
        for mid in ids:groups.setdefault(find(mid),[]).append(mid)
        segments=[];current=[]
        def fits(part):
            chosen=set(part)
            sub={'title':title,'sources':[x for x in sources if x['id'] in chosen],
                 'relations':[x for x in relations if x['newer'] in chosen and x['older'] in chosen],
                 'dates':{k:v for k,v in dates.items() if k in chosen}}
            return len(part)<=20 and len(sub['relations'])<=40 and len(make_brief_prompt(sub))<=14000
        for group in groups.values():
            if not fits(group):raise ValueError('relationship group too large')
            if current and not fits(current+group):segments.append(current);current=[]
            current.extend(group)
        if current:segments.append(current)
        if len(segments)>6:raise ValueError('too many segments')
        return {'strategy':'segmented_v1','title':title,'event_id':event['id'],
                'fingerprint':snap['digest'],'date_token':self._event_date_token(scope,event['id']),
                'as_of':cutoff.isoformat(),'sources':sources,'relations':relations,'dates':dates,'segments':segments}

    @staticmethod
    def model_segment_part(snapshot,part_no):
        if type(part_no) is not int or not 0<=part_no<len(snapshot['segments']):raise ValueError('part')
        ids=set(snapshot['segments'][part_no])
        return {'title':snapshot['title'],'sources':[x for x in snapshot['sources'] if x['id'] in ids],
                'relations':[x for x in snapshot['relations'] if x['newer'] in ids and x['older'] in ids],
                'dates':{k:v for k,v in snapshot['dates'].items() if k in ids}}

    @staticmethod
    def _segment_cache_key(snapshot,part,provider):
        if not isinstance(provider,str) or not provider.strip():raise ValueError('provider')
        # 摘要依据须与当前完整模型提示、事件和提供商一致。
        payload=json.dumps(['segment-cache-v1',provider,snapshot['event_id'],make_brief_prompt(part)],ensure_ascii=False)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def read_segment_cache(self,scope,snapshot,part_no,provider):
        if self.model_segment_snapshot(scope,snapshot['title'],snapshot['as_of'])!=snapshot:raise ValueError('changed')
        part=self.model_segment_part(snapshot,part_no);key=self._segment_cache_key(snapshot,part,provider)
        row=self.flow.db.execute('SELECT result_json,created_at FROM dl_segment_cache WHERE scope=? AND cache_key=?',
                                 (scope,key)).fetchone()
        if not row:return None
        try:
            stamp=datetime.fromisoformat(row['created_at']);age=datetime.now(timezone.utc)-stamp
            if stamp.utcoffset() is None or age<timedelta(0) or age>timedelta(days=7):return None
            validate_brief_output(row['result_json'],part)
        except (ValueError,TypeError,OverflowError):return None
        return row['result_json']

    def save_segment_cache(self,scope,snapshot,part_no,provider,raw):
        if self.model_segment_snapshot(scope,snapshot['title'],snapshot['as_of'])!=snapshot:raise ValueError('changed')
        part=self.model_segment_part(snapshot,part_no);validate_brief_output(raw,part)
        key=self._segment_cache_key(snapshot,part,provider)
        with self.flow.db:
            self.flow.db.execute('INSERT OR REPLACE INTO dl_segment_cache VALUES(?,?,?,?)',
                (scope,key,raw,datetime.now(timezone.utc).isoformat()))
            self.flow.db.execute('''DELETE FROM dl_segment_cache WHERE scope=? AND cache_key NOT IN
                (SELECT cache_key FROM dl_segment_cache WHERE scope=? ORDER BY created_at DESC,cache_key DESC LIMIT 128)''',
                (scope,scope))

    def segment_cache_count(self,scope):
        return self.flow.db.execute('SELECT count(*) FROM dl_segment_cache WHERE scope=?',(scope,)).fetchone()[0]

    def clear_segment_cache(self,scope):
        with self.flow.db:
            cursor=self.flow.db.execute('DELETE FROM dl_segment_cache WHERE scope=?',(scope,))
        return cursor.rowcount

    def _check_segment_results(self,snapshot,raw_parts,raw_final):
        if not isinstance(raw_parts,list) or len(raw_parts)!=len(snapshot['segments']):raise ValueError('incomplete')
        results=[validate_brief_output(raw,self.model_segment_part(snapshot,i)) for i,raw in enumerate(raw_parts)]
        final=validate_brief_output(raw_final,snapshot)
        if not any(final['source_id']==r['source_id'] and final['quote']==r['quote'] for r in results):
            raise ValueError('final quote must match a segment citation')
        return results,final

    def save_segmented_brief_draft(self,scope,snapshot,raw_parts,raw_final,reused=None):
        if self.model_segment_snapshot(scope,snapshot['title'],snapshot['as_of'])!=snapshot:raise ValueError('changed')
        parts,result=self._check_segment_results(snapshot,raw_parts,raw_final);draft='BS-'+uuid.uuid4().hex[:16]
        if reused is None:reused=[False]*len(parts)
        if len(reused)!=len(parts) or any(type(x) is not bool for x in reused):raise ValueError('invalid cache flags')
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_brief_model_drafts VALUES(?,?,?,?,?,NULL)',
                (scope,draft,json.dumps(snapshot,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
            for i,item in enumerate(parts):
                self.flow.db.execute('INSERT INTO dl_brief_segment_results (scope,draft_id,part_no,result_json,cache_reused) VALUES(?,?,?,?,?)',
                                     (scope,draft,i,json.dumps(item,ensure_ascii=False),int(reused[i])))
        return self.read_model_brief_draft(scope,draft)

    def save_model_brief_draft(self,scope,snapshot,raw):
        current=self.model_brief_snapshot(scope,snapshot['title'],snapshot['as_of'])
        if current!=snapshot:raise ValueError('snapshot changed')
        result=validate_brief_output(raw,current);draft='BS-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_brief_model_drafts VALUES(?,?,?,?,?,NULL)',
                (scope,draft,json.dumps(current,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_model_brief_draft(scope,draft)

    def read_model_brief_draft(self,scope,draft_id):
        row=self.flow.db.execute('SELECT * FROM dl_brief_model_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        saved=json.loads(row['snapshot_json']);result=json.loads(row['result_json'])
        try:
            if saved.get('strategy')=='segmented_v1':
                current=self.model_segment_snapshot(scope,saved['title'],saved['as_of'])
                rows=list(self.flow.db.execute(
                    'SELECT result_json,cache_reused FROM dl_brief_segment_results WHERE scope=? AND draft_id=? ORDER BY part_no',
                    (scope,draft_id)))
                raw_parts=[r['result_json'] for r in rows]
                self._check_segment_results(saved,raw_parts,json.dumps(result,ensure_ascii=False))
                segments=[json.loads(x) for x in raw_parts];reused=[bool(r['cache_reused']) for r in rows]
            else:
                current=self.model_brief_snapshot(scope,saved['title'],saved['as_of']);segments=[];reused=[]
                validate_brief_output(json.dumps(result,ensure_ascii=False),current)
            valid=current==saved
        except (ValueError,TypeError,KeyError):valid=False;segments=[];reused=[]
        return {'draft_id':draft_id,'snapshot':saved,'result':result,'current':valid,'segment_results':segments,'segment_reused':reused,
                'accepted_brief_id':row['accepted_brief_id']}

    def confirm_model_brief_draft(self,scope,draft_id):
        draft=self.read_model_brief_draft(scope,draft_id)
        if not draft['current']:raise ValueError('stale draft')
        if draft['accepted_brief_id'] is not None:return draft['accepted_brief_id']
        snap=draft['snapshot'];result=draft['result']
        source=next(x for x in snap['sources'] if x['id']==result['source_id'])
        with self.flow.db:
            cursor=self.flow.db.execute('''INSERT INTO dl_source_briefs
              (scope,event_id,source_id,source_sha,source_fingerprint,date_token,excerpt,brief,created_at,origin)
              VALUES(?,?,?,?,?,?,?,?,?,?)''',(scope,snap['event_id'],source['id'],source['sha'],snap['fingerprint'],
                snap['date_token'],result['quote'],result['summary'],datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'model_reviewed'))
            brief_id=cursor.lastrowid
            self.flow.db.execute('UPDATE dl_brief_model_drafts SET accepted_brief_id=? WHERE scope=? AND draft_id=?',
                                 (brief_id,scope,draft_id))
        return brief_id

    def save_source_brief(self,scope,title,source_id,excerpt,brief):
        """人工编写的派生摘要，绑定事件及原文，不作为事实校验。"""
        if (not isinstance(excerpt,str) or not 2<=len(excerpt)<=120 or
            not isinstance(brief,str) or not 1<=len(brief)<=180 or
            not brief.strip() or '\x00' in excerpt or '\x00' in brief):
            raise ValueError('invalid brief')
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        source=next((r for r in snap['rows'] if r['id']==source_id),None)
        if not source or excerpt not in source['content']:raise ValueError('excerpt not found')
        with self.flow.db:
            cursor=self.flow.db.execute('''INSERT INTO dl_source_briefs
              (scope,event_id,source_id,source_sha,source_fingerprint,date_token,excerpt,brief,created_at,origin)
              VALUES(?,?,?,?,?,?,?,?,?,?)''',(scope,event['id'],source_id,source['source_sha256'],snap['digest'],
                    self._event_date_token(scope,event['id']),excerpt,brief,datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'human_written'))
        return cursor.lastrowid

    def read_source_brief(self,scope,title):
        event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
        row=self.flow.db.execute('''SELECT * FROM dl_source_briefs WHERE scope=? AND event_id=?
                                  ORDER BY id DESC LIMIT 1''',(scope,event['id'])).fetchone()
        if not row:return None
        source=next((r for r in snap['rows'] if r['id']==row['source_id']),None)
        valid=bool(source and row['source_sha']==source['source_sha256'] and row['excerpt'] in source['content']
                   and row['source_fingerprint']==snap['digest']
                   and row['date_token']==self._event_date_token(scope,event['id']))
        return {'id':row['id'],'brief':row['brief'],'excerpt':row['excerpt'],'source_id':row['source_id'],
                'source_time':source['recorded_at'] if source else None,'created_at':row['created_at'],
                'current':valid,'origin':row['origin']}

    def _event_annotated_dates(self,scope,snap,cutoff):
        """只返回在请求时点可用、且与已审核原话一致的人工日期标注。"""
        known={row['id']:row['source_sha256'] for row in snap['rows']};out={}
        rows=self.flow.db.execute('''SELECT a.message_id,a.occurred_date,a.source_sha,a.annotated_at
            FROM dl_date_annotations a JOIN e417_phase p ON p.scope=a.scope AND p.message_id=a.message_id
            WHERE a.scope=? AND p.event_id=? AND a.id=(SELECT max(d.id) FROM dl_date_annotations d
                WHERE d.scope=a.scope AND d.message_id=a.message_id)''',(scope,snap['id']))
        for row in rows:
            try:
                annotated=datetime.fromisoformat(row['annotated_at'])
                if annotated.utcoffset() is None or annotated>=cutoff:continue
                if row['message_id'] not in known or row['source_sha']!=known[row['message_id']]:continue
                value=row['occurred_date']
                if value is not None and date.fromisoformat(value).isoformat()==value:out[row['message_id']]=value
            except (ValueError,TypeError,OverflowError):continue
        return out
