"""Store 的 organize 领域方法（mixin，由 core.Store 组合）。"""
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

class OrganizeMixin:
    def _weak_candidate_details(self,scope,row,events):
        """Rank lexical candidates and preserve a short path back to original lines."""
        grams=self._character_pairs(row['content'])
        if len(grams)<2:return []
        try:
            cutoff=datetime.fromisoformat(row['recorded_at'])
            if cutoff.tzinfo is None:return []
        except (TypeError,ValueError):return []
        documents=[]
        denied=self._negated_events(scope,row['content'],events)
        allowed={event['id']:event for event in events if event['id'] not in denied}
        postings=self.lexical_index.candidates(scope,grams)
        for eid,item in postings.items():
            event=allowed.get(eid)
            if not event or len(item['terms'])<2:continue
            snap=self.flow._snapshot(scope,eid)
            if not snap or snap['stale']:continue
            if self.lexical_index.fingerprint(snap)!=item['fingerprint']:
                with self.flow.db:self.flow.db.execute('INSERT OR IGNORE INTO dl_lex_dirty VALUES(?,?)',(scope,eid))
                continue
            event={'id':snap['id'],'title':snap['title']}
            try:
                times=[datetime.fromisoformat(x['recorded_at']) for x in snap['rows']]
                if any(t.tzinfo is None or t>=cutoff for t in times):continue
            except (TypeError,ValueError):continue
            documents.append((event,snap,item['terms'],max(times)))
        frequencies=Counter(g for _,_,shared,_ in documents for g in shared)
        results=[]
        historical=bool(re.search(r'最早|最初|起初|原本|原来|以前|之前|过去|当时|上次|去年|历史|曾经|当年|那时候|不喜欢|不再|讨厌|\d{4}年|\d{4}[-/]\d{1,2}',row['content']))
        for event,snap,shared,latest in documents:
            if len(shared)<2:continue
            score=sum(1.0+math.log((len(documents)+1)/(frequencies[g]+1)) for g in shared)
            # Longest exact substrings retain their source ID; they are not entity labels.
            sources=[(r['id'],r['content']) for r in reversed(snap['rows'])]+[(None,event['title'])]
            clues=[];covered=set();query=row['content']
            for size in range(min(8,len(query)),1,-1):
                for start in range(len(query)-size+1):
                    term=query[start:start+size]
                    if any(i in covered for i in range(start,start+size)) or not all('\u4e00'<=c<='\u9fff' for c in term):continue
                    if size==2 and term in _GENERIC_GRAMS:continue
                    source=next(((mid,text) for mid,text in sources if term in text),None)
                    if source:
                        clues.append({'text':term,'source_id':source[0]})
                        covered.update(range(start,start+size))
                        if len(clues)>=3:break
                if len(clues)>=3:break
            result={'event_id':event['id'],'title':event['title'],'score':score,
                    'fingerprint':snap['digest'],'clues':clues}
            if self.preference_rank_enabled:
                # 两个 UTC 日期的不同自述才产生微小排序加分，不创建事实或候选。
                signals=self._preference_signal(scope,snap)
                result['preference_boost']=0.15 if len(signals)>=2 and not historical else 0.0
                result['preference_source_ids']=[x['id'] for x in signals[:2]] if result['preference_boost'] else []
            if self.recency_rank_enabled:
                # 不修改来源时间；近期只是排序线索，并非新颖度或事实有效性。
                age_days=max(0.0,(cutoff-latest).total_seconds()/86400.0)
                result['recency_boost']=0.0 if historical else 0.25*max(0.0,1.0-age_days/30.0)
            results.append(result)
        important=set()
        if self.event_priority_enabled:
            important={r['event_id'] for r in self.flow.db.execute(
                "SELECT event_id FROM dl_event_priority WHERE scope=? AND priority='high'",(scope,))}
            for item in results:item['priority_boost']=0.25 if item['event_id'] in important else 0.0
        return sorted(results,key=lambda x:(-(x['score']+x.get('priority_boost',0)+x.get('recency_boost',0)+x.get('preference_boost',0)),x['event_id']))[:3]

    def _weak_candidates(self,scope,row,events):
        return [x['event_id'] for x in self._weak_candidate_details(scope,row,events)]

    def explain_weak_candidates(self,scope,seq):
        source=self.model_sources(scope,[seq])[0]
        return self._weak_candidate_details(scope,source,self.flow._events(scope))

    def propose(self,scope,seq,*,semantic=False,weak=None):
        """Generate traceable, unconfirmed links. Does not approve or modify source."""
        if type(seq) is not int or seq<1:raise ValueError('invalid sequence')
        db=self.flow.db
        row=db.execute("SELECT * FROM dl_capture WHERE scope=? AND seq=? AND state='pending'",(scope,seq)).fetchone()
        if not row:raise ValueError('pending capture unavailable')
        if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
            raise ValueError('capture integrity failed')
        text=row['content'];events=self.flow._events(scope)
        matches=[(event['id'],method) for event,method in self._named_events(scope,text,events)]
        if weak is None:weak=self.weak_association_enabled
        if weak:
            existing={x[0] for x in matches}
            matches.extend((eid,'原文词组弱匹配') for eid in self._weak_candidates(scope,row,events) if eid not in existing)
        semantic_error=False
        if semantic:
            try:
                semantic_ids=self._index().search(scope,text)
                existing={x[0] for x in matches}
                denied=self._negated_events(scope,text,events)
                matches.extend((eid,'语义相似') for eid in semantic_ids if eid not in existing and eid not in denied)
            except Exception:semantic_error=True
        titles={e['id']:e['title'] for e in events};valid=[]
        for eid,method in matches[:6]:
            if eid not in titles:continue
            snap=self.flow._snapshot(scope,eid)
            if not snap or snap['stale']:continue
            valid.append({'event_id':eid,'title':titles[eid],'method':method,'fingerprint':snap['digest']})
        # Propose a new event only from an explicit author-supplied event marker.
        # Avoid inventing titles from free text or treating it as a fact.
        marker=re.match(r'^【事件[：:]([^【】\r\n]{1,80})】',text)
        new_title=None
        if marker:
            title=marker.group(1).strip()
            if title and title not in titles.values():
                new_title={'title':title,'method':'原文明确事件标记','status':'UNCONFIRMED'}
        elif not valid:
            # A whole original sentence may serve as a rough label; no rewriting,
            # inference or slicing. Skip questions and generic short utterances.
            raw=text.strip()
            if (raw==text and 5<=len(raw)<=36 and '\n' not in raw and
                not any(c in raw for c in '?？|【】') and
                any(word in raw for word in ('打算','计划','准备','想去','想学')) and
                raw not in titles.values()):
                new_title={'title':raw,'method':'原句作为临时标题（未概括）','status':'UNCONFIRMED'}
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with db:
            db.execute('DELETE FROM dl_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
            for item in valid:
                db.execute('INSERT INTO dl_suggestions VALUES(?,?,?,?,?,?,?)',
                    (scope,seq,item['event_id'],row['content_sha'],item['fingerprint'],item['method'],now))
            db.execute('DELETE FROM dl_new_title_suggestions WHERE scope=? AND capture_seq=?',(scope,seq))
            if new_title:
                db.execute('INSERT INTO dl_new_title_suggestions VALUES(?,?,?,?,?,?)',
                    (scope,seq,row['content_sha'],new_title['title'],new_title['method'],now))
        return {'seq':seq,'content':text,'candidates':valid,'semantic_attempted':semantic,
                'semantic_error':semantic_error,'new_event_candidate':new_title,'status':'UNCONFIRMED'}

    def organize_batch(self,scope,limit=10,after=0):
        """Refresh rule-based suggestions for a bounded batch; no implicit BGE calls."""
        if type(limit) is not int or not 1<=limit<=10:raise ValueError('invalid batch size')
        if type(after) is not int or after<0:raise ValueError('invalid cursor')
        rows=self.flow.db.execute("SELECT seq FROM dl_capture WHERE scope=? AND state='pending' AND seq>? ORDER BY seq LIMIT ?",(scope,after,limit)).fetchall()
        outcomes=[]
        for row in rows:
            try:
                result=self.propose(scope,row['seq'],semantic=False)
                outcomes.append({'seq':row['seq'],'candidates':result['candidates'],
                                 'new_event_candidate':result['new_event_candidate'],'status':'UNCONFIRMED'})
            except Exception:
                outcomes.append({'seq':row['seq'],'candidates':[],'new_event_candidate':None,'status':'ERROR'})
        return outcomes

    def group_pending_suggestions(self,scope,after=0):
        """按连续采集记录生成可核对候选，不写入关联或模型调用。"""
        if type(after) is not int or not 0<=after<=9223372036854775807:
            raise ValueError('invalid cursor')
        records=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at,state FROM dl_capture
            WHERE scope=? AND seq>? ORDER BY seq LIMIT 10''',(scope,after)).fetchall()
        events=self.flow._events(scope);eligible={}
        def latest_time(event):
            if event['id'] in eligible:return eligible[event['id']]
            try:
                snap=self.flow._snapshot(scope,event['id'])
                times=[datetime.fromisoformat(r['recorded_at']) for r in snap['rows']] if snap and not snap['stale'] else []
                latest=max(times) if times and all(t.utcoffset() is not None for t in times) else None
            except (TypeError,ValueError,OverflowError):latest=None
            eligible[event['id']]=latest
            return latest
        groups=[];current=[];current_event=None;previous_time=None
        def finish():
            nonlocal current,current_event,previous_time
            if len(current)>=2:groups.append({'title':current_event['title'],'items':current})
            current=[];current_event=None;previous_time=None
        for row in records:
            try:
                if row['state']!='pending' or hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                    finish();continue
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None or re.search(r'不是|并非|不指|别说|不对|记错|认错|错了',row['content']):
                    finish();continue
                named=self._named_events(scope,row['content'],events)
                if len(named)>1:finish();continue
                event=named[0][0] if named else None
                method=named[0][1] if named else ''
                within=previous_time is not None and 0<=(stamp-previous_time).total_seconds()<=180
                if (event is None and current and within and _FOLLOWUP_UPDATE.match(row['content'].strip())):
                    event=current_event;method='相邻可能更新'
                if not event:finish();continue
                latest=latest_time(event)
                if latest is None or latest>=stamp:finish();continue
                if current and (event['id']!=current_event['id'] or not within):finish()
                current_event=event
                current.append({'seq':row['seq'],'content':row['content'],'recorded_at':row['recorded_at'],'method':method})
                previous_time=stamp
            except (TypeError,ValueError,OverflowError):finish()
        finish()
        return {'groups':groups,'next_cursor':records[-1]['seq'] if records else None,'scanned':len(records)}

    def rule_group_draft(self,scope,after=0):
        """把一页规则候选整理为可复核草稿，不调用模型或写入原话关联。"""
        db=self.flow.db
        db.execute('BEGIN IMMEDIATE')
        try:
            result=self._rule_group_draft_locked(scope,after)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    def _rule_group_draft_locked(self,scope,after):
        suggestions=self.group_pending_suggestions(scope,after)
        selected=[];titles=[]
        for group in suggestions['groups']:
            title=group['title']
            if title not in titles and len(titles)>=4:break
            items=[x for x in group['items'] if len(x['content'])>=2]
            if not items:continue
            if title not in titles:titles.append(title)
            selected.append({'title':title,'items':items})
        if not selected:return {'draft':None,'next_cursor':suggestions['next_cursor'],'scanned':suggestions['scanned']}
        def snapshot_for(groups):
            seqs=[x['seq'] for group in groups for x in group['items']]
            names=list(dict.fromkeys(group['title'] for group in groups))
            return self.model_group_snapshot(scope,seqs,names,rule_batch=True)
        try:snapshot=snapshot_for(selected)
        except ValueError:
            # 不同事件的时间边界可能无法合并；保留第一组并从其后继续。
            selected=selected[:1];snapshot=snapshot_for(selected)
        merged={}
        for group in selected:
            for item in group['items']:
                merged.setdefault(group['title'],[]).append({'seq':item['seq'],'quote':item['content'][:80]})
        raw=json.dumps({'groups':[{'title':title,'items':items} for title,items in merged.items()]},ensure_ascii=False)
        draft=self.save_group_draft(scope,snapshot,raw,origin='rule')
        return {'draft':draft,'next_cursor':max(x['seq'] for g in selected for x in g['items']),
                'scanned':suggestions['scanned']}

    def _update_candidates(self,scope,source,events,*,include_weak=True):
        """Find possible event labels, preserving ambiguity as an unconfirmed clue."""
        content=source['content']
        if not _UPDATE_CUES.search(content):return []
        def named(text):return [event for event,_ in self._named_events(scope,text,events)]
        direct=self._named_events(scope,content,events)
        if direct:return [(event,'完整标题' if method=='标题字面匹配' else '人工别名') for event,method in direct]
        aliases=[r['alias'] for r in self.flow.db.execute('SELECT alias FROM dl_event_alias WHERE scope=?',(scope,))]
        if any(x['title'] in content for x in events) or any(x in content for x in aliases):return []
        def weak_match(item):
            if not include_weak or not self.weak_association_enabled or re.search(r'不是|并非|不指|别说',item['content']):return []
            details=self._weak_candidate_details(scope,item,events)
            if len(details)!=1:return []
            detail=details[0]
            source_id=next((x['source_id'] for x in detail['clues'] if x['source_id'] and len(x['text'])>=4),None)
            event=next((x for x in events if x['id']==detail['event_id']),None)
            return [({**event,'clue_source_id':source_id},'历史原文词组匹配')] if event and source_id else []
        matches=weak_match(source)
        if matches:return matches
        if not _FOLLOWUP_UPDATE.match(content.strip()):return []
        cursor=source
        for distance in range(1,5):
            previous=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
                WHERE scope=? AND seq<? ORDER BY seq DESC LIMIT 1''',(scope,cursor['seq'])).fetchone()
            if not previous or hashlib.sha256(previous['content'].encode('utf-8')).hexdigest()!=previous['content_sha']:return []
            try:
                start=datetime.fromisoformat(previous['recorded_at']);end=datetime.fromisoformat(cursor['recorded_at'])
                if start.tzinfo is None or end.tzinfo is None or not 0<=(end-start).total_seconds()<=180:return []
            except (ValueError,TypeError):return []
            candidates=named(previous['content'])
            if candidates:
                method='前一条采集记录的标题' if distance==1 else '前面采集记录的标题（连续更新线索）'
                return [(candidates[0],method)] if len(candidates)==1 else []
            if any(x['title'] in previous['content'] for x in events) or any(x in previous['content'] for x in aliases):return []
            matches=weak_match(previous)
            if matches:return [(matches[0][0],'前文'+matches[0][1])]
            if not _FOLLOWUP_UPDATE.match(previous['content'].strip()):return []
            cursor=previous
        return []

    def update_clues(self,scope,after=0,limit=10):
        """Review a bounded page of pending records without promoting any clue."""
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=10:
            raise ValueError('invalid cursor')
        records=self.pending(scope,after=after)[:limit]
        events=self.flow._events(scope);out=[]
        for row in records:
            source=self.model_sources(scope,[row['seq']])[0]
            matches=[]
            if _UPDATE_CUES.search(source['content']):
                for event,method in self._update_candidates(scope,source,events):
                    title=event['title']
                    snap=self.flow._snapshot(scope,event['id'])
                    if not snap or snap['stale']:continue
                    try:
                        timestamp=datetime.fromisoformat(source['recorded_at'])
                        old=[r for r in snap['rows'] if datetime.fromisoformat(r['recorded_at'])<timestamp]
                    except (TypeError,ValueError):continue
                    reference=next((r for r in old if r['id']==event.get('clue_source_id')),None) if event.get('clue_source_id') else (old[-1] if old else None)
                    if reference:
                        matches.append({'title':title,'method':method,'old_id':reference['id'],'old_content':reference['content'],
                                        'old_time':reference['recorded_at']})
            out.append({'seq':source['seq'],'content':source['content'],'recorded_at':source['recorded_at'],
                        'matches':matches[:3]})
        return out

    def rule_update_drafts(self,scope,after=0):
        """生成至多五条有完整新旧原话的规则对照，不自动确认关系。"""
        rows=self.update_clues(scope,after=after);drafts=[];last=None
        for row in rows:
            last=row['seq']
            if len(row['matches'])!=1:continue
            match=row['matches'][0]
            try:
                snapshot=self.contrast_snapshot(scope,row['seq'],match['title'])
                old=next((x for x in snapshot['history'] if x['id']==match['old_id']),None)
                if not old or not 2<=len(old['content'])<=400 or not 2<=len(row['content'])<=400:continue
                result={'relation':'uncertain','explanation':'字面更新线索；较早原话未必相关，请人工核对。',
                        'old_id':old['id'],'old_quote':old['content'],'new_quote':row['content']}
                drafts.append(self.save_contrast_draft(scope,snapshot,json.dumps(result,ensure_ascii=False),origin='rule'))
            except (ValueError,TypeError,OverflowError):continue
            if len(drafts)>=5:break
        return {'drafts':drafts,'next_cursor':last,'scanned':len(rows)}

    def confirm_contrast_batch(self,scope,draft_ids,kind):
        """同一事务内校验和写入；失败时回滚本次批次。"""
        if (not isinstance(draft_ids,list) or not 1<=len(draft_ids)<=5 or
            any(not isinstance(x,str) or not re.fullmatch(r'C-[0-9a-f]{16}',x) for x in draft_ids) or
            len(set(draft_ids))!=len(draft_ids) or kind not in ('corrects','supersedes','related')):
            raise ValueError('invalid selection')
        db=self.flow.db;failed=None
        db.execute('BEGIN IMMEDIATE')
        try:
            with self.flow.core.deferred_writes():
                self._check_contrast_batch(scope,draft_ids,kind)
                for did in draft_ids:
                    failed=did
                    items=self._check_contrast_batch(scope,draft_ids,kind)
                    item=next(x for x in items if x['draft_id']==did)
                    self.link_capture_relation(scope,item['seq'],item['title'],item['old_id'],kind)
            db.commit()
        except Exception:
            db.rollback()
            if failed is None:raise
            return {'completed':[],'failed':failed}
        return {'completed':list(draft_ids),'failed':None}

    def _check_contrast_batch(self,scope,draft_ids,kind):
        db=self.flow.db;items=[];groups={};seen=set()
        for did in draft_ids:
            draft=db.execute('SELECT * FROM dl_contrast_drafts WHERE scope=? AND draft_id=?',(scope,did)).fetchone()
            if not draft:raise ValueError('draft missing')
            manifest=json.loads(draft['snapshot_json']);result=json.loads(draft['result_json'])
            seq=draft['pending_seq']
            if seq in seen:raise ValueError('duplicate source')
            seen.add(seq)
            capture=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
            if not capture or capture['state'] not in ('pending','linked'):raise ValueError('capture unavailable')
            if (hashlib.sha256(capture['content'].encode('utf-8')).hexdigest()!=manifest['pending_sha'] or
                capture['content_sha']!=manifest['pending_sha'] or capture['recorded_at']!=manifest['pending_time']):
                raise ValueError('capture changed')
            prior=self.flow.writer.status(scope,'captured:'+capture['event_hash']);linked=None
            if prior:
                if prior['state']=='approved_link':
                    if prior['candidate_event_id']!=manifest['event_id'] or prior['source_sha256']!=manifest['pending_sha']:
                        raise ValueError('approval changed')
                    linked=prior['message_id']
                elif prior['state']!='pending_review':raise ValueError('approval unavailable')
            if capture['state']=='linked' and (not linked or capture['linked_source']!=linked or capture['linked_title']!=draft['event_title']):
                raise ValueError('linked capture changed')
            item={'draft_id':did,'seq':seq,'title':draft['event_title'],'manifest':manifest,
                  'result':result,'capture':dict(capture),'linked':linked,'old_id':result['old_id']}
            items.append(item);groups.setdefault(manifest['event_id'],[]).append(item)
        for eid,group in groups.items():
            snap=self.flow._snapshot(scope,eid)
            if not snap or snap['stale'] or any(x['title']!=snap['title'] for x in group):raise ValueError('event changed')
            linked={x['linked'] for x in group if x['linked']}
            if len(linked)!=sum(x['linked'] is not None for x in group):raise ValueError('duplicate linked source')
            rows,edges=self.flow._event_rows(scope,eid)
            original=[r for r in rows if r['id'] not in linked]
            old_edges=[r for r in edges if r['newer'] not in linked and r['older'] not in linked]
            expected={(x['linked'],x['old_id'],kind) for x in group if x['linked']}
            if any((r['newer'],r['older'],r['kind']) not in expected for r in edges
                   if r['newer'] in linked or r['older'] in linked):raise ValueError('new relation changed')
            digest=_fingerprint(original,old_edges)
            for item in group:
                manifest=item['manifest']
                if digest!=manifest['fingerprint']:raise ValueError('history changed')
                history=[{'id':r['id'],'content':r['content'],'recorded_at':r['recorded_at']}
                         for r in original if r['id'] in manifest['old_ids']]
                if [r['id'] for r in history]!=manifest['old_ids']:raise ValueError('history missing')
                validate_contrast(json.dumps(item['result'],ensure_ascii=False),{'history':history,'pending':item['capture']})
                old=next((r for r in history if r['id']==item['old_id']),None)
                if not old:raise ValueError('old source missing')
                before=datetime.fromisoformat(old['recorded_at']);after=datetime.fromisoformat(item['capture']['recorded_at'])
                if before.utcoffset() is None or after.utcoffset() is None or before>=after:
                    raise ValueError('source chronology invalid')
        return items

    def _pending_update_hint(self,scope,title,*,snapshot=None):
        """Bounded hint scan; no unreviewed text enters the context."""
        rows=self.flow.db.execute("SELECT seq,content,content_sha,recorded_at FROM dl_capture WHERE scope=? AND state='pending' ORDER BY seq DESC LIMIT 101",(scope,)).fetchall()
        events=self.flow._events(scope)
        known=None
        def history_terms():
            nonlocal known
            if known is not None:return known
            known={}
            for old in snapshot['rows']:
                at=datetime.fromisoformat(old['recorded_at'])
                if at.tzinfo is None:raise ValueError('unknown historical time')
                text=old['content']
                for i in range(len(text)-3):
                    phrase=text[i:i+4]
                    if (all('\u4e00'<=c<='\u9fff' for c in phrase) and
                        len(self._character_pairs(phrase))>=2 and
                        (phrase not in known or at<known[phrase])):known[phrase]=at
            return known
        for row in rows:
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
                return True
            if any(event['title']==title for event,_ in self._update_candidates(scope,row,events,include_weak=False)):
                return True
            if snapshot and _UPDATE_CUES.search(row['content']) and not re.search(r'不是|并非|不指|别说',row['content']):
                cursor=row
                for distance in range(5):
                    text=cursor['content']
                    phrases={text[i:i+4] for i in range(len(text)-3)
                             if all('\u4e00'<=c<='\u9fff' for c in text[i:i+4])
                             and len(self._character_pairs(text[i:i+4]))>=2}
                    try:
                        at=datetime.fromisoformat(cursor['recorded_at'])
                        if at.tzinfo is None:return True
                        terms=history_terms()
                        if any(phrase in terms and terms[phrase]<at for phrase in phrases):return True
                    except (TypeError,ValueError):return True
                    if distance==4 or not _FOLLOWUP_UPDATE.match(text.strip()):break
                    previous=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
                        WHERE scope=? AND seq<? ORDER BY seq DESC LIMIT 1''',(scope,cursor['seq'])).fetchone()
                    if not previous:break
                    if hashlib.sha256(previous['content'].encode('utf-8')).hexdigest()!=previous['content_sha']:return True
                    try:
                        start=datetime.fromisoformat(previous['recorded_at']);end=datetime.fromisoformat(cursor['recorded_at'])
                        if start.tzinfo is None or end.tzinfo is None or not 0<=(end-start).total_seconds()<=180:break
                    except (TypeError,ValueError):break
                    cursor=previous
        return len(rows)>100
