"""Store 的 追问：指代追问与上一轮衔接（mixin，由 ContextMixin 组合）。"""
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



class ContextFollowupMixin:
    def _auto_followup(self,scope,focus,budget,cutoff,current_message_id,excluded):
        """承接近期系统候选，重新核验完整来源；不把指代推测视为确认。"""
        if not isinstance(current_message_id,str) or not 1<=len(current_message_id)<=256:return ''
        manifest=focus.get('auto_sources')
        if not isinstance(manifest,list) or not 1<=len(manifest)<=5:return ''
        db=self.flow.db;version=db.execute('PRAGMA data_version').fetchone()[0]
        try:
            ids=[x['seq'] for x in manifest]
            if any(type(x) is not int for x in ids) or ids!=sorted(set(ids)):return ''
            from ..auto_model import separated
            if any(separated(db,scope,a,b) for i,a in enumerate(ids) for b in ids[i+1:]):return ''
            current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode()).hexdigest()
            if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
                WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return ''
            from ..auto_organize import read_group
            items=[]
            for entry in manifest:
                seq=entry['seq']
                if 'pending:'+str(seq) in excluded:return ''
                if self._auto_source_conflict(scope,seq):return ''
                row=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
                if (not row or row['state']!='pending' or row['event_hash']==current_hash or row['content_sha']!=entry['sha'] or
                    row['recorded_at']!=entry['time'] or hashlib.sha256(row['content'].encode()).hexdigest()!=entry['sha']):return ''
                t=datetime.fromisoformat(row['recorded_at'])
                if t.utcoffset() is None or t>=cutoff:return ''
                group=read_group(db,scope,seq)
                if group and group['members']!=ids:return ''
                key=self._index().model_key if self.model_dir else None
                member=db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?',(scope,seq,key)).fetchone()
                if member:
                    linked=[r['seq'] for r in db.execute('SELECT seq FROM dl_auto_topic_members WHERE scope=? AND topic_id=? AND model_key=? ORDER BY seq',(scope,member['topic_id'],key))]
                    if linked!=ids:return ''
                following=db.execute('SELECT seq,content,recorded_at FROM dl_capture WHERE scope=? AND seq>? ORDER BY seq LIMIT 1',(scope,seq)).fetchone()
                if following and following['seq'] not in ids and _FOLLOWUP_UPDATE.match(following['content'].strip()):
                    later=datetime.fromisoformat(following['recorded_at'])
                    if later.utcoffset() is None or 0<=(later-t).total_seconds()<=180:return ''
                item={'序号':seq,'原话':row['content'],'记录时间':row['recorded_at']}
                if db.execute('SELECT 1 FROM dl_import_provenance WHERE scope=? AND seq=?',(scope,seq)).fetchone():item['时间来源']='导入文件，未经独立核验'
                items.append(item)
            payload={'待审记录序号':ids[0],'待审相关序号':ids,'可能相关原话':items,
                '说明':'可能延续上一轮的未确认线索；指代及事件关系不确定'}
            if self.model_dir:
                from ..auto_model import unresolved_hint_sources
                if unresolved_hint_sources(db,scope,self._index().model_key,ids):
                    payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
            result=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            if db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
            return result if len(result)<=budget else ''
        except (TypeError,KeyError,ValueError,OverflowError):return ''

    def context_turn(self,scope,query,focus=None,budget=600,*,exclude_ids=None,current_message_id=None,as_of=None):
        """Resolve only short, explicit follow-ups to the preceding named event."""
        followup=re.fullmatch(r'(?:那件事|这件事|那个计划|这个计划)?(?:后来|最后|最初|现在|结果)(?:呢|怎么样|如何|是什么|怎么样了)?[？?。！!]*',query)
        # 对“那个背包是什么颜色”这类短追问，仅在原话中存在相同细节时延续。
        detail=re.fullmatch(r'(?:那个|这个|那次的|这次的|它的|之前说的)([\u4e00-\u9fff]{2,12}?)(?:呢|怎么样(?:了)?|是什么(?:颜色|时间|价格)?|多少钱|在哪(?:里)?|什么(?:颜色|时间|价格|时候)|[？?])[？?。！!]*',query)
        if detail and (re.search(r'不是|并非|不对|记错|错了|纠正|别说|不指',query) or
                       self._named_events(scope,query,self.flow._events(scope))):detail=None
        if followup and self.automatic_memory_enabled and focus and focus.get('auto_sources'):
            if focus.get('remaining',0)<1:return '',None
            cutoff=as_of if as_of is not None else datetime.now(timezone.utc)
            if not isinstance(cutoff,datetime) or cutoff.utcoffset() is None:return '',None
            if focus.get('cross_query') or focus.get('topic_query'):
                try:
                    query_before=focus.get('cross_query') or focus.get('topic_query');manifest=focus['auto_sources']
                    if not isinstance(query_before,str) or not 1<=len(query_before)<=400 or not isinstance(manifest,list) or not 2<=len(manifest)<=budget//10:return '',None
                    version=self.flow.db.execute('PRAGMA data_version').fetchone()[0]
                    stamp=cutoff.astimezone(timezone.utc).isoformat()
                    if focus.get('cross_query'):
                        from ..auto_cross_update import cross_update_context
                        blocked,packet=cross_update_context(self.flow.db,self._index().model_key,self._index().embedder,scope,query_before,
                            stamp,budget,current_message_id,set(exclude_ids or ()))
                        if not blocked:packet=''
                    else:
                        if len(manifest)>10:return '',None
                        packet=self._auto_topic_context(scope,query_before,stamp,budget,current_message_id,set(exclude_ids or ()),
                                                        after_gap=self.capture_gap_boundary(scope))
                    data=json.loads(packet) if packet else {}
                    ids=data.get('待审相关序号')
                    previous=[entry['seq'] for entry in manifest]
                    if (not isinstance(ids,list) or any(type(x) is not int for x in ids) or
                        ids!=sorted(set(ids)) or not set(previous).issubset(ids)):return '',None
                    for entry in manifest:
                        row=self.flow.db.execute('SELECT content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq=? AND state=\'pending\'',(scope,entry['seq'])).fetchone()
                        if not row or row['content_sha']!=entry['sha'] or row['recorded_at']!=entry['time']:return '',None
                    refreshed=[]
                    for seq in ids:
                        row=self.flow.db.execute('SELECT content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq=? AND state=\'pending\'',(scope,seq)).fetchone()
                        if not row:return '',None
                        refreshed.append({'seq':seq,'sha':row['content_sha'],'time':row['recorded_at']})
                    if self.flow.db.execute('PRAGMA data_version').fetchone()[0]!=version:return '',None
                    return packet,{**focus,'auto_sources':refreshed,'remaining':focus['remaining']-1}
                except (KeyError,TypeError,ValueError):return '',None
            packet=self._auto_followup(scope,focus,budget,cutoff,current_message_id,set(exclude_ids or ()))
            return (packet,{**focus,'remaining':focus['remaining']-1}) if packet else ('',None)
        if followup or detail:
            if not focus or focus.get('remaining',0)<1 or focus['event_id'] in (exclude_ids or ()):return '',None
            snap=self.flow._snapshot(scope,focus['event_id'])
            if (not snap or snap['stale'] or snap['digest']!=focus['digest'] or
                focus.get('alias_token')!=self._alias_token(scope,snap['id'])):return '',None
            if detail:
                terms=self._character_pairs(detail.group(1))
                if not terms or not any(term in row['content'] for row in snap['rows'] for term in terms):
                    # 无法把细节绑定到前一事件时，按普通检索处理。
                    packet=self.context_packet(scope,query,budget=budget,exclude_ids=exclude_ids,
                                               current_message_id=current_message_id,as_of=as_of)
                    return packet,None
            anchor=focus.get('anchor_name','')
            anchored=self._named_events(scope,anchor,self.flow._events(scope)) if anchor else []
            if len(anchored)!=1 or anchored[0][0]['id']!=snap['id']:return '',None
            prefix='可能延续上一轮提到的事件，指代未确认；如果不相关请忽略。\n'
            packet=self.context_packet(scope,snap['title'],budget=budget-len(prefix),time_hint=query,
                                       current_message_id=current_message_id,as_of=as_of)
            if not packet:return '',None
            # The referent is a conversational guess, even after a named event.
            next_focus={**focus,'remaining':focus['remaining']-1}
            return (prefix+packet,next_focus) if len(prefix+packet)<=budget else ('',None)
        events=self.flow._events(scope)
        exact=[e for e,_ in self._named_events(scope,query,events)]
        packet=self.context_packet(scope,query,budget=budget,exclude_ids=exclude_ids,current_message_id=current_message_id,as_of=as_of)
        if (not packet or len(exact)!=1 or re.search(r'[「」『』“”"\']|不是|不对|错了|纠正|并非',query)):
            return packet,None
        snap=self.flow._snapshot(scope,exact[0]['id'])
        if not snap or snap['stale']:return packet,None
        names=[snap['title']]+self.list_aliases(scope,snap['title'])
        anchor=next((name for name in names if name in query and not re.search(
            r'(?:不是|并非|不指|别说|并不是)\s*'+re.escape(name),query)),None)
        if not anchor:return packet,None
        return packet,{'event_id':snap['id'],'digest':snap['digest'],'remaining':2,
                       'alias_token':self._alias_token(scope,snap['id']),'anchor_name':anchor}
