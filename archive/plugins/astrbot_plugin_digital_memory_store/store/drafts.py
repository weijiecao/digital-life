"""Store 的 drafts 领域方法（mixin，由 core.Store 组合）。"""
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

class DraftMixin:
    def model_sources(self,scope,seqs):
        if (not isinstance(seqs,list) or not 1<=len(seqs)<=5 or
            any(type(x) is not int or x<1 for x in seqs) or len(set(seqs))!=len(seqs)):
            raise ValueError('invalid source selection')
        out=[]
        for seq in seqs:
            row=self.flow.db.execute("SELECT seq,content,content_sha,recorded_at,state FROM dl_capture WHERE scope=? AND seq=?",(scope,seq)).fetchone()
            if not row or row['state']!='pending':raise ValueError('pending source missing')
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                raise ValueError('source changed')
            out.append(dict(row))
        return out

    def new_group_sources(self,scope,seqs):
        if (not isinstance(seqs,list) or not 1<=len(seqs)<=20 or
            any(type(x) is not int or x<1 for x in seqs) or len(set(seqs))!=len(seqs)):
            raise ValueError('invalid selection')
        out=[];cutoff=datetime.now(timezone.utc)
        for seq in seqs:
            row=self.flow.db.execute("SELECT seq,content,content_sha,recorded_at,state FROM dl_capture WHERE scope=? AND seq=?",(scope,seq)).fetchone()
            if not row or row['state']!='pending':raise ValueError('source missing')
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:raise ValueError('changed')
            stamp=datetime.fromisoformat(row['recorded_at'])
            if stamp.utcoffset() is None or stamp>=cutoff:raise ValueError('source time')
            out.append(dict(row))
        return out

    def next_new_group_sources(self,scope,after=0):
        """读取一个有界待审批次，并复用原有来源完整性检查。"""
        if type(after) is not int or not 0<=after<=9223372036854775807:raise ValueError('invalid cursor')
        rows=self.flow.db.execute('''SELECT seq FROM dl_capture WHERE scope=? AND state='pending'
            AND seq>? ORDER BY seq LIMIT 20''',(scope,after)).fetchall()
        if not rows:return []
        return self.new_group_sources(scope,[r['seq'] for r in rows])

    def _new_group_title_available(self,scope,title):
        return (not self.flow.writer._event_by_title(scope,title) and not self.flow.db.execute(
            'SELECT 1 FROM dl_event_alias WHERE scope=? AND alias=?',(scope,title)).fetchone())

    def save_new_group_draft(self,scope,sources,raw):
        current=self.new_group_sources(scope,[x['seq'] for x in sources])
        if current!=sources:raise ValueError('sources changed')
        result=validate_new_groups(raw,current)
        if any(not self._new_group_title_available(scope,g['title']) for g in result['groups']):
            raise ValueError('title conflicts with existing event or alias')
        manifest=[{'seq':x['seq'],'sha':x['content_sha'],'time':x['recorded_at']} for x in current]
        draft='NG-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_new_group_drafts VALUES(?,?,?,?,?)',
                (scope,draft,json.dumps(manifest,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_new_group_draft(scope,draft)

    def read_new_group_draft(self,scope,draft_id):
        db=self.flow.db
        draft=db.execute('SELECT * FROM dl_new_group_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not draft:raise ValueError('draft missing')
        manifest=json.loads(draft['manifest_json']);result=json.loads(draft['result_json'])
        assignments={x['seq']:g['title'] for g in result['groups'] for x in g['items']}
        owned={r['title']:r['event_id'] for r in db.execute(
            'SELECT title,event_id FROM dl_new_group_events WHERE scope=? AND draft_id=?',(scope,draft_id))}
        if not set(owned)<={g['title'] for g in result['groups']}:raise ValueError('invalid event mapping')
        sources=[];approved={title:set() for title in owned}
        for entry in manifest:
            row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,entry['seq'])).fetchone()
            if (not row or row['state'] not in ('pending','linked') or row['content_sha']!=entry['sha'] or
                row['recorded_at']!=entry['time'] or hashlib.sha256(row['content'].encode()).hexdigest()!=entry['sha']):
                raise ValueError('source changed')
            title=assignments.get(row['seq']);prior=self.flow.writer.status(scope,'captured:'+row['event_hash'])
            if row['state']=='linked' and (not title or row['linked_title']!=title):raise ValueError('link changed')
            if prior and prior['state']=='approved_link':
                if (title not in owned or prior['candidate_event_id']!=owned[title] or prior['source_sha256']!=entry['sha'] or
                    (row['state']=='linked' and row['linked_source']!=prior['message_id'])):raise ValueError('approval changed')
                approved[title].add(prior['message_id'])
            elif row['state']=='linked':raise ValueError('missing approved source')
            sources.append({'seq':row['seq'],'content':row['content'],'state':row['state']})
        validate_new_groups(json.dumps(result,ensure_ascii=False),sources)
        for group in result['groups']:
            title=group['title'];matches=self.flow.writer._event_by_title(scope,title)
            alias=db.execute('SELECT 1 FROM dl_event_alias WHERE scope=? AND alias=?',(scope,title)).fetchone()
            if alias:raise ValueError('alias conflict')
            if title not in owned:
                if matches:raise ValueError('title conflict')
                continue
            if len(matches)!=1 or matches[0]['id']!=owned[title]:raise ValueError('event changed')
            rows,relations=self.flow._event_rows(scope,owned[title])
            if relations or {x['id'] for x in rows}!=approved[title]:raise ValueError('event history changed')
            if any(hashlib.sha256(x['content'].encode()).hexdigest()!=x['source_sha256'] for x in rows):
                raise ValueError('event source changed')
        return {'draft_id':draft_id,'result':result,'sources':sources,'owned':owned}

    @atomic_write
    def confirm_new_group_draft(self,scope,draft_id,seqs=None):
        db=self.flow.db
        draft=self.read_new_group_draft(scope,draft_id)
        allowed={x['seq'] for g in draft['result']['groups'] for x in g['items']}
        if seqs is None:chosen=allowed
        else:
            if (not isinstance(seqs,list) or not 1<=len(seqs)<=20 or
                any(type(x) is not int for x in seqs) or len(set(seqs))!=len(seqs) or not set(seqs)<=allowed):
                raise ValueError('invalid selection')
            chosen=set(seqs)
        for group in draft['result']['groups']:
            title=group['title']
            if title not in draft['owned'] and any(x['seq'] in chosen for x in group['items']):
                eid='E-'+uuid.uuid4().hex[:16]
                db.execute('INSERT INTO events(scope,id,title) VALUES(?,?,?)',(scope,eid,title))
                db.execute('INSERT INTO dl_new_group_events VALUES(?,?,?,?)',(scope,draft_id,title,eid))
        completed=[]
        for group in draft['result']['groups']:
            for item in group['items']:
                if item['seq'] not in chosen:continue
                try:self.link_capture(scope,item['seq'],group['title'])
                except Exception:return {'completed':completed,'failed':item['seq']}
                completed.append(item['seq'])
        return {'completed':completed,'failed':None}

    def model_group_snapshot(self,scope,seqs,titles,*,rule_batch=False):
        """准备有限的待审原文和事件，留待模型结果生成后重新校验。"""
        if rule_batch and len(seqs)>10:raise ValueError('too many sources')
        sources=self.new_group_sources(scope,seqs) if rule_batch else self.model_sources(scope,seqs)
        if not isinstance(titles,list) or not 1<=len(titles)<=4 or len(set(titles))!=len(titles):
            raise ValueError('invalid event selection')
        times=[datetime.fromisoformat(x['recorded_at']) for x in sources]
        if any(t.utcoffset() is None for t in times):raise ValueError('invalid capture time')
        earliest=min(times);events=[]
        for title in titles:
            event=self._event_by_title(scope,title);snap=self.flow._snapshot(scope,event['id'])
            if len(snap['rows'])>1000:raise ValueError('event too large')
            old_times=[datetime.fromisoformat(r['recorded_at']) for r in snap['rows']]
            if any(t.utcoffset() is None or t>=earliest for t in old_times):
                raise ValueError('event is not earlier than selected messages')
            events.append({'title':title,'id':event['id'],'fingerprint':snap['digest'],
                           'history':[r['content'] for r in snap['rows'][-2:]]})
            if rule_batch:events[-1]['alias_token']=self._alias_token(scope,event['id'])
        return {'sources':sources,'events':events}

    def save_group_draft(self,scope,snapshot,raw,*,origin='model'):
        if origin not in ('model','rule'):raise ValueError('invalid draft origin')
        current=self.model_group_snapshot(scope,[x['seq'] for x in snapshot['sources']],
                                          [x['title'] for x in snapshot['events']],rule_batch=origin=='rule')
        if current!=snapshot:raise ValueError('sources changed')
        result=validate_group_output(raw,current)
        manifest={'sources':[{'seq':x['seq'],'sha':x['content_sha'],'time':x['recorded_at']} for x in current['sources']],
                  'events':[{'title':x['title'],'id':x['id'],'fingerprint':x['fingerprint']} for x in current['events']]}
        if origin=='rule':
            for item,event in zip(manifest['events'],current['events']):item['alias_token']=event['alias_token']
        draft='FG-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_group_drafts(scope,draft_id,manifest_json,result_json,created_at,origin) VALUES(?,?,?,?,?,?)',
                (scope,draft,json.dumps(manifest,ensure_ascii=False),json.dumps(result,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds'),origin))
        return {'draft_id':draft,'result':result,'manifest':manifest,'origin':origin,
                'sources':[{'seq':x['seq'],'content':x['content'],'state':x['state']} for x in current['sources']]}

    def _validate_group_draft(self,scope,draft_id):
        db=self.flow.db
        draft=db.execute('SELECT * FROM dl_group_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not draft:raise ValueError('draft missing')
        manifest=json.loads(draft['manifest_json']);result=json.loads(draft['result_json'])
        assignments={item['seq']:group['title'] for group in result['groups'] for item in group['items']}
        events={x['title']:x for x in manifest['events']};sources=[];linked_by_event={x['id']:set() for x in manifest['events']}
        for item in manifest['sources']:
            row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,item['seq'])).fetchone()
            if (not row or row['state'] not in ('pending','linked') or row['content_sha']!=item['sha'] or
                row['recorded_at']!=item['time'] or hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=item['sha']):
                raise ValueError('source changed')
            title=assignments.get(row['seq'])
            if title is None and row['state']!='pending':raise ValueError('unassigned source changed')
            if title and row['state']=='linked' and row['linked_title']!=title:
                raise ValueError('source cannot be linked')
            prior=self.flow.writer.status(scope,'captured:'+row['event_hash'])
            if prior and prior['state']=='approved_link':
                if (not title or title not in events or prior['candidate_event_id']!=events[title]['id'] or
                    prior['source_sha256']!=item['sha']):
                    raise ValueError('event changed')
                if row['state']=='linked' and row['linked_source']!=prior['message_id']:
                    raise ValueError('linked source mismatch')
                linked_by_event[events[title]['id']].add(prior['message_id'])
            elif row['state']=='linked':raise ValueError('missing approved source')
            sources.append({'seq':row['seq'],'content':row['content'],'state':row['state']})
        validate_group_output(json.dumps(result,ensure_ascii=False),{'sources':sources,'events':manifest['events']})
        for event in manifest['events']:
            matches=self.flow.writer._event_by_title(scope,event['title'])
            if len(matches)!=1 or matches[0]['id']!=event['id']:raise ValueError('event changed')
            if draft['origin']=='rule' and event.get('alias_token')!=self._alias_token(scope,event['id']):
                raise ValueError('event aliases changed')
            snap=self.flow._snapshot(scope,event['id'])
            if not snap or snap['stale']:raise ValueError('event source changed')
            rows,edges=self.flow._event_rows(scope,event['id']);ours=linked_by_event[event['id']]
            old_rows=[r for r in rows if r['id'] not in ours]
            old_edges=[r for r in edges if r['newer'] not in ours and r['older'] not in ours]
            # 确认期间出现额外的关系不能被视为本次关联的副作用。
            if len(old_edges)!=len(edges) or _fingerprint(old_rows,old_edges)!=event['fingerprint']:
                raise ValueError('event history changed')
        return {'draft_id':draft_id,'result':result,'manifest':manifest,'sources':sources,'origin':draft['origin']}

    def read_group_draft(self,scope,draft_id):
        return self._validate_group_draft(scope,draft_id)

    @atomic_write
    def confirm_group_draft(self,scope,draft_id,seqs=None):
        draft=self._validate_group_draft(scope,draft_id)
        allowed={item['seq'] for group in draft['result']['groups'] for item in group['items']}
        if seqs is None:chosen=allowed
        else:
            if (not isinstance(seqs,list) or not 1<=len(seqs)<=10 or
                any(type(x) is not int for x in seqs) or len(set(seqs))!=len(seqs) or not set(seqs)<=allowed):
                raise ValueError('invalid selection')
            chosen=set(seqs)
        completed=[]
        for group in draft['result']['groups']:
            for item in group['items']:
                if item['seq'] not in chosen:continue
                try:self.link_capture(scope,item['seq'],group['title'])
                except Exception:return {'completed':completed,'failed':item['seq']}
                completed.append(item['seq'])
        return {'completed':completed,'failed':None}

    def multi_suggest(self,scope,seqs,*,semantic=False):
        """Find unconfirmed historical candidates for manually selected pending lines."""
        if (not isinstance(seqs,list) or not 2<=len(seqs)<=4 or
            any(type(x) is not int or x<1 for x in seqs) or seqs!=sorted(seqs)):
            raise ValueError('select two to four records in chronological sequence')
        sources=self.model_sources(scope,seqs)
        times=[datetime.fromisoformat(x['recorded_at']) for x in sources]
        if any(t.tzinfo is None for t in times) or times!=sorted(times):
            raise ValueError('invalid source chronology')
        query='\n'.join(x['content'] for x in sources)
        eligible={}
        for event in self.flow._events(scope):
            snap=self.flow._snapshot(scope,event['id'])
            if not snap or snap['stale']:continue
            try:
                timestamps=[datetime.fromisoformat(row['recorded_at']) for row in snap['rows']]
                if any(t.tzinfo is None or t>=times[0] for t in timestamps):continue
            except (TypeError,ValueError):continue
            eligible[event['id']]=event
        matches=[]
        for event in eligible.values():
            title=event['title']
            if any(title in line['content'] and not re.search(
                r'(?:不是|并非|不指|别说|并不是)\s*'+re.escape(title),line['content']) for line in sources):
                matches.append({'title':title,'method':'标题字面匹配'})
        semantic_error=False
        if semantic and eligible:
            try:
                ids=self._index().search(scope,query)
                existing={x['title'] for x in matches}
                for eid in ids:
                    event=eligible.get(eid)
                    if event and event['title'] not in existing:
                        matches.append({'title':event['title'],'method':'多轮语义相似'})
                        existing.add(event['title'])
            except Exception:semantic_error=True
        return {'candidates':matches[:3],'semantic_error':semantic_error,'status':'UNCONFIRMED'}

    def save_model_draft(self,scope,sources,raw):
        current=self.model_sources(scope,[x['seq'] for x in sources])
        if current!=sources:raise ValueError('sources changed while generating')
        candidate=validate_output(raw,current)
        manifest=[{'seq':x['seq'],'sha256':x['content_sha'],'recorded_at':x['recorded_at']} for x in current]
        draft='G-'+uuid.uuid4().hex[:16]
        with self.flow.db:
            self.flow.db.execute('INSERT INTO dl_model_drafts VALUES(?,?,?,?,?)',
                (scope,draft,json.dumps(manifest,ensure_ascii=False),json.dumps(candidate,ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))
        return self.read_model_draft(scope,draft)

    def contrast_snapshot(self,scope,seq,title):
        pending=self.model_sources(scope,[seq])[0]
        events=self.flow.writer._event_by_title(scope,title)
        if len(events)!=1:raise ValueError('event not unique')
        snap=self.flow._snapshot(scope,events[0]['id'])
        if not snap or snap['stale']:raise ValueError('source unavailable')
        new_time=datetime.fromisoformat(pending['recorded_at'])
        # Only already recorded sources may serve as old history; no new-line leakage.
        older=[r for r in snap['rows'] if datetime.fromisoformat(r['recorded_at'])<new_time]
        if not older:raise ValueError('no older reviewed source')
        return {'pending':pending,'event_id':events[0]['id'],'event_title':title,
                'fingerprint':snap['digest'],
                'history':[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at']} for r in older[-3:]]}

    def save_contrast_draft(self,scope,snapshot,raw,*,origin='model'):
        if origin not in ('model','rule'):raise ValueError('invalid draft origin')
        current=self.contrast_snapshot(scope,snapshot['pending']['seq'],snapshot['event_title'])
        if current!=snapshot:raise ValueError('source changed during generation')
        result=validate_contrast(raw,current)
        draft='C-'+uuid.uuid4().hex[:16]
        # Persist fingerprints and source IDs; do not duplicate the entire original text.
        manifest={'pending_sha':snapshot['pending']['content_sha'],'pending_time':snapshot['pending']['recorded_at'],
                  'event_id':snapshot['event_id'],'fingerprint':snapshot['fingerprint'],
                  'old_ids':[x['id'] for x in snapshot['history']]}
        with self.flow.db:
            self.flow.db.execute('''INSERT INTO dl_contrast_drafts
                (scope,draft_id,pending_seq,event_title,snapshot_json,result_json,created_at,origin)
                VALUES(?,?,?,?,?,?,?,?)''',
                (scope,draft,snapshot['pending']['seq'],snapshot['event_title'],json.dumps(manifest),
                 json.dumps(result,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds'),origin))
        return self.read_contrast_draft(scope,draft)

    def read_contrast_draft(self,scope,draft_id):
        row=self.flow.db.execute('SELECT * FROM dl_contrast_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        manifest=json.loads(row['snapshot_json']);result=json.loads(row['result_json'])
        try:
            snap=self.contrast_snapshot(scope,row['pending_seq'],row['event_title'])
            current={'pending_sha':snap['pending']['content_sha'],'pending_time':snap['pending']['recorded_at'],
                     'event_id':snap['event_id'],'fingerprint':snap['fingerprint'],
                     'old_ids':[x['id'] for x in snap['history']]}
            if current!=manifest:raise ValueError('source changed')
            validate_contrast(json.dumps(result,ensure_ascii=False),snap)
            status='UNREVIEWED'
        except Exception:status='STALE'
        return {'draft_id':draft_id,'status':status,'result':result,'event_title':row['event_title'],
                'pending_seq':row['pending_seq'],'origin':row['origin']}

    @atomic_write
    def confirm_contrast(self,scope,draft_id,kind,*,validate_only=False):
        """Explicitly link a contrast source and record the operator-selected relation.

        The writer commits in stages. Retrying this operation is safe after an
        interrupted link; historical provenance is checked again before each stage.
        """
        if kind not in ('corrects','supersedes','related'):raise ValueError('invalid relation')
        db=self.flow.db
        draft=db.execute('SELECT * FROM dl_contrast_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not draft:raise ValueError('draft missing')
        manifest=json.loads(draft['snapshot_json']);result=json.loads(draft['result_json'])
        capture=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,draft['pending_seq'])).fetchone()
        if not capture or capture['state'] not in ('pending','linked'):raise ValueError('source unavailable')
        if (hashlib.sha256(capture['content'].encode('utf-8')).hexdigest()!=manifest['pending_sha'] or
            capture['recorded_at']!=manifest['pending_time']):raise ValueError('source changed')
        if capture['state']=='linked' and capture['linked_title']!=draft['event_title']:
            raise ValueError('source linked to a different event')
        origin='captured:'+capture['event_hash']
        prior=self.flow.writer.status(scope,origin)
        linked_id=None
        if prior and prior['state']=='approved_link':
            if prior['candidate_event_id']!=manifest['event_id']:raise ValueError('linked event mismatch')
            linked_id=prior['message_id']
        snap=self.flow._snapshot(scope,manifest['event_id'])
        if not snap or snap['stale'] or snap['title']!=draft['event_title']:raise ValueError('event changed')
        rows,edges=self.flow._event_rows(scope,manifest['event_id'])
        original=[r for r in rows if r['id']!=linked_id]
        old_edges=[r for r in edges if linked_id not in (r['newer'],r['older'])]
        if _fingerprint(original,old_edges)!=manifest['fingerprint']:raise ValueError('history changed')
        history=[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at']} for r in original
                 if r['id'] in manifest['old_ids']]
        if [r['id'] for r in history]!=manifest['old_ids']:raise ValueError('history missing')
        validate_contrast(json.dumps(result,ensure_ascii=False),{'history':history,'pending':dict(capture)})
        if linked_id and any(r['newer']==linked_id and r['older']==result['old_id'] and r['kind']!=kind for r in edges):
            raise ValueError('different relation already exists')
        if linked_id and any(linked_id in (r['newer'],r['older']) and
                             (r['newer'],r['older'],r['kind'])!=(linked_id,result['old_id'],kind) for r in edges):
            raise ValueError('linked source relations changed')
        if validate_only:return {'event_id':manifest['event_id']}
        if capture['state']=='pending':linked_id=self.link_capture(scope,capture['seq'],draft['event_title'])
        elif linked_id!=capture['linked_source']:raise ValueError('linked source mismatch')
        self.relate_sources(scope,linked_id,result['old_id'],kind)
        return {'newer_id':linked_id,'older_id':result['old_id'],'kind':kind}

    def read_model_draft(self,scope,draft_id):
        row=self.flow.db.execute('SELECT * FROM dl_model_drafts WHERE scope=? AND draft_id=?',(scope,draft_id)).fetchone()
        if not row:raise ValueError('draft missing')
        manifest=json.loads(row['source_manifest']);result=json.loads(row['result_json'])
        try:
            current=self.model_sources(scope,[x['seq'] for x in manifest])
            now=[{'seq':x['seq'],'sha256':x['content_sha'],'recorded_at':x['recorded_at']} for x in current]
            if now!=manifest:raise ValueError('source changed')
            validate_output(json.dumps(result,ensure_ascii=False),current)
            state='UNREVIEWED'
        except Exception:state='STALE'
        return {'draft_id':draft_id,'status':state,'result':result,'created_at':row['created_at']}
