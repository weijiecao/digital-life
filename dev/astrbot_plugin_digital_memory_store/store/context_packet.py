"""Store 的 上下文包：待审/多事件/背景拼装与预算（mixin，由 ContextMixin 组合）。"""
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



class ContextPacketMixin:
    def _possible_update_peer(self,scope,row,window=180,max_chars=40):
        """相邻 180 秒内、含变化词且未处理的短句，视为该原话的“可能更新”（仅线索）。"""
        try: stamp=datetime.fromisoformat(row['recorded_at'])
        except (TypeError,ValueError): return None
        if stamp.tzinfo is None: return None
        nxt=self.flow.db.execute('''SELECT seq,content,content_sha,recorded_at,state FROM dl_capture
            WHERE scope=? AND seq>? ORDER BY seq LIMIT 1''',(scope,row['seq'])).fetchone()
        if not nxt or nxt['state']!='pending': return None
        text=nxt['content'].strip()
        if not text or len(text)>max_chars or not _UPDATE_CUES.search(text): return None
        try: t=datetime.fromisoformat(nxt['recorded_at'])
        except (TypeError,ValueError): return None
        if t.tzinfo is None or not 0<=(t-stamp).total_seconds()<=window: return None
        if hashlib.sha256(nxt['content'].encode('utf-8')).hexdigest()!=nxt['content_sha']: return None
        return nxt

    def _pending_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        """Recall one line or a bounded possible update chain; never the current message."""
        if (not self.pending_context_enabled or not isinstance(current_message_id,str) or
            not 1<=len(current_message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query)):
            return ''
        phrases={query[i:i+4] for i in range(len(query)-3)
                 if all('\u4e00'<=c<='\u9fff' for c in query[i:i+4])
                 and len(self._character_pairs(query[i:i+4]))>=2}
        if not phrases:return ''
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode('utf-8')).hexdigest()
        try:
            limit=datetime.fromisoformat(cutoff)
            boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if limit.utcoffset() is None or (boundary and boundary.utcoffset() is None):return ''
        except (TypeError,ValueError,OverflowError):return ''
        version=self.flow.db.execute('PRAGMA data_version').fetchone()[0];found=[]
        for seq in self.pending_index.candidates(scope,self._character_pairs(query)):
            if 'pending:'+str(seq) in excluded:continue
            if self._auto_source_conflict(scope,seq):continue
            row=self.flow.db.execute('''SELECT c.seq,c.content,c.content_sha,c.recorded_at,c.event_hash,c.state,p.content_sha AS indexed_sha
                FROM dl_capture c JOIN dl_pending_index p ON p.scope=c.scope AND p.seq=c.seq
                WHERE c.scope=? AND c.seq=?''',(scope,seq)).fetchone()
            if (not row or row['state']!='pending' or row['event_hash']==current_hash or
                row['content'].strip()==query.strip()):continue
            if row['content_sha']!=row['indexed_sha'] or hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:continue
            try:
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None or stamp>=limit or (boundary and stamp<=boundary):continue
            except (TypeError,ValueError):continue
            if not any(p in row['content'] for p in phrases):continue
            found.append(row)
            if len(found)>5:return ''
        if not found:return ''
        if self.automatic_memory_enabled and self.model_dir:
            try:
                model_key=self._index().model_key
                for candidate in found:
                    if self.flow.db.execute('''SELECT 1 FROM dl_auto_topic_members m WHERE m.scope=? AND m.seq=? AND m.model_key=?
                        AND (SELECT COUNT(*) FROM dl_auto_topic_members n WHERE n.scope=m.scope AND n.topic_id=m.topic_id AND n.model_key=m.model_key)>1''',
                        (scope,candidate['seq'],model_key)).fetchone():return ''
            except Exception:return ''
        found.sort(key=lambda item:item['seq'])
        row=found[0]
        def neighbor(seq,previous):
            direction='<' if previous else '>'
            ordering='DESC' if previous else 'ASC'
            return self.flow.db.execute(f'''SELECT seq,content,content_sha,recorded_at,event_hash,state FROM dl_capture
                WHERE scope=? AND seq {direction} ? ORDER BY seq {ordering} LIMIT 1''',(scope,seq)).fetchone()
        def same_window(first,second):
            try:
                a=datetime.fromisoformat(first['recorded_at']);b=datetime.fromisoformat(second['recorded_at'])
                return a.tzinfo is not None and b.tzinfo is not None and 0<=(b-a).total_seconds()<=180
            except (TypeError,ValueError):return False
        def acceptable(item):
            if (not item or item['state']!='pending' or 'pending:'+str(item['seq']) in excluded or
                item['event_hash']==current_hash or item['content'].strip()==query.strip() or
                hashlib.sha256(item['content'].encode('utf-8')).hexdigest()!=item['content_sha']):return False
            try:
                stamp=datetime.fromisoformat(item['recorded_at'])
                return stamp.utcoffset() is not None and stamp<limit and (not boundary or stamp>boundary)
            except (TypeError,ValueError):return False
        # Move back to the base message for an update, never expose a partial chain.
        backward_hops=0
        while _FOLLOWUP_UPDATE.match(row['content'].strip()):
            previous=neighbor(row['seq'],True)
            if not previous or not same_window(previous,row) or not acceptable(previous):return ''
            row=previous
            backward_hops+=1
            if backward_hops>=5:return ''
        chain=[row]
        while True:
            following=neighbor(chain[-1]['seq'],False)
            if (following and same_window(chain[-1],following) and
                hashlib.sha256(following['content'].encode('utf-8')).hexdigest()!=following['content_sha']):return ''
            if not following or not same_window(chain[-1],following):break
            if not (_FOLLOWUP_UPDATE.match(following['content'].strip()) or
                    (len(following['content'])<=40 and _UPDATE_CUES.search(following['content']))):break
            # The current user's words already appear in the request itself.
            if following['event_hash']==current_hash:break
            if not acceptable(following) or len(chain)>=5:return ''
            chain.append(following)
        if not {x['seq'] for x in found}.issubset({x['seq'] for x in chain}):return ''
        payload={'待审记录序号':row['seq'],'未整理历史原话':row['content'],'记录时间':row['recorded_at'],
                 '说明':'这是未关联的旧用户原话，仅为可能相关线索；指代与内容尚未确认'}
        if self.automatic_memory_enabled and self.model_dir:
            from ..auto_model import unresolved_hint_sources
            if unresolved_hint_sources(self.flow.db,scope,self._index().model_key,{x['seq'] for x in chain}):
                payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
        imported=[x['seq'] for x in chain if self.flow.db.execute(
            'SELECT 1 FROM dl_import_provenance WHERE scope=? AND seq=?',(scope,x['seq'])).fetchone()]
        if imported:payload['导入时间说明']={'序号':imported,'说明':'时间由导入文件提供，未经独立核验'}
        if len(chain)>1:
            updates=[{'序号':x['seq'],'原话':x['content'],'记录时间':x['recorded_at']} for x in chain[1:]]
            payload['待审相关序号']=[x['seq'] for x in chain]
            payload['后续可能更新']=updates[0]
            if len(updates)>1:payload['进一步可能更新']=updates[1:]
            payload['说明']='相邻采集原话可能有更新关系；内容、事件和指代均尚未确认'
        if boundary:payload['采集情况']='先前存在已检测到的采集缺口，历史可能不完整；缺失内容无法恢复'
        output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if self.flow.db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
        return output if len(output)<=budget else ''

    def _two_explicit_mentions(self,scope,query,events):
        """两个事件必须各有不重叠的明确名字，避免标题包含造成歧义。"""
        if len(events)!=2 or re.search(r'[「」『』“”"\']|不是|并非|别说|不指|不去|错了|记错|纠正',query):return False
        aliases={event['id']:[] for event in events}
        for row in self.flow.db.execute('SELECT event_id,alias FROM dl_event_alias WHERE scope=?',(scope,)):
            if row['event_id'] in aliases:aliases[row['event_id']].append(row['alias'])
        names=[[event['title']]+aliases[event['id']] for event in events]
        if set(names[0])&set(names[1]):return False
        spans=[]
        for choices in names:
            found=[]
            for name in choices:
                if not name:continue
                found.extend((match.start(),match.end()) for match in re.finditer(re.escape(name),query))
            spans.append(found)
        return any(a[1]<=b[0] or b[1]<=a[0] for a in spans[0] for b in spans[1])

    def _multi_event_packet(self,scope,query,events,budget,*,cutoff,current_message_id=None,data_version=None):
        """先预留每个事件的优先关系组，再分配和回收剩余字符预算。"""
        # 其他连接在本次准备及预算分配期间写入时，丢弃结果而不是复用旧快照。
        if data_version is None:data_version=self.flow.db.execute('PRAGMA data_version').fetchone()[0]
        if not self._two_explicit_mentions(scope,query,events):return ''
        wrapper={'多个事件':[],'说明':'分别是不同事件的历史原话，不表示事件之间存在关联'}
        overhead=len(json.dumps(wrapper,ensure_ascii=False,separators=(',',':')))+len(events)-1
        available=budget-overhead
        if available<=0:return ''
        # 同一请求中每个事件只做一次来源、时间和关系准备，不跨请求缓存。
        plans=[self._prepare_context_event(scope,event,query,cutoff=cutoff,
                                          current_message_id=current_message_id) for event in events]
        if not all(plans):return ''
        def retrieve(index,limit,minimum=False):
            packet=plans[index](limit,minimum)
            if not packet:return ''
            try:data=json.loads(packet)
            except (TypeError,ValueError):return ''
            return packet if isinstance(data,dict) and data.get('原话') else ''
        minimum=[retrieve(i,available,True) for i in range(len(events))]
        if not all(minimum):return ''
        remaining=available-sum(map(len,minimum))
        if remaining<0:return ''
        # 不拆分任何关系组；若当前事件用不满，未用预算转交下一个事件。
        first=retrieve(0,len(minimum[0])+remaining//2)
        if not first:return ''
        second_budget=available-len(first)
        second=retrieve(1,second_budget)
        if not second:return ''
        spare=available-len(first)-len(second)
        if spare>0:
            first=retrieve(0,len(first)+spare)
            if not first:return ''
        if len(first)+len(second)>available:return ''
        wrapper['多个事件']=[json.loads(first),json.loads(second)]
        result=json.dumps(wrapper,ensure_ascii=False,separators=(',',':'))
        if self.flow.db.execute('PRAGMA data_version').fetchone()[0]!=data_version:return ''
        return result if len(result)<=budget else ''

    def _background_context(self,scope,query,budget,current_message_id,cutoff):
        """每轮上下文兜底：取较早保存的原话作为背景，绝不当作本轮答案。"""
        if not self.always_context_enabled:return ''
        if not isinstance(current_message_id,str) or not 1<=len(current_message_id)<=256:return ''
        try: limit=datetime.fromisoformat(cutoff)
        except (TypeError,ValueError):return ''
        if limit.tzinfo is None:return ''
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode('utf-8')).hexdigest()
        rows=self.flow.db.execute('''SELECT seq,content,content_sha,event_hash,recorded_at
            FROM dl_capture WHERE scope=? AND state='pending' ORDER BY seq''',(scope,)).fetchall()
        candidates=[]
        for row in rows:
            if row['event_hash']==current_hash:continue
            try: stamp=datetime.fromisoformat(row['recorded_at'])
            except (TypeError,ValueError):continue
            if stamp.utcoffset() is None or stamp>=limit:continue
            if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:continue
            candidates.append(row)
        if not candidates:return ''
        chosen=candidates[:2]  # 较早的原话 = 当前窗口之外的长期背景
        latest=candidates[-1]
        if latest['seq'] not in {item['seq'] for item in chosen}:
            chosen.append(latest)
        chosen.sort(key=lambda item:item['seq'])
        payload={'背景上下文':'以下为该会话较早保存的原话（未确认，可能过时）；仅作背景，不代表本轮答案',
                 '最近原话':[{'序号':item['seq'],'原话':item['content'],
                          '记录时间':item['recorded_at']} for item in chosen],
                 '说明':'本轮没有直接匹配的记忆；以上仅为背景线索，不要断言为当前事实'}
        out=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        while len(out)>budget and len(payload['最近原话'])>1:
            payload['最近原话']=payload['最近原话'][:-1]
            out=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        return out if len(out)<=budget else ''

    def context_packet(self,scope,query,budget=600,*,time_hint=None,exclude_ids=None,current_message_id=None,as_of=None):
        """Bounded source packet; similarity is association only, not evidence."""
        if not 1<=len(query)<=400: return ''
        request_time=as_of if as_of is not None else datetime.now(timezone.utc)
        if not isinstance(request_time,datetime) or request_time.utcoffset() is None:return ''
        data_version=self.flow.db.execute('PRAGMA data_version').fetchone()[0] if self.multi_event_context_enabled else None
        events=self.flow._events(scope)
        # Avoid treating negated or quoted titles as positive references.
        exact=[e for e,_ in self._named_events(scope,query,events)]
        # Scope ambiguity prevents source promotion, even if vector search returns one.
        if len(exact)==1:
            e=exact[0]
            return self._context_event(scope,e,time_hint or query,budget,cutoff=request_time,current_message_id=current_message_id)
        # 默认延续多标题歧义保护；实验模式仅允许两个明确且不重叠的标题。
        if len(exact)>1:
            if (not self.multi_event_context_enabled or len(exact)!=2 or
                any(e['id'] in (exclude_ids or ()) for e in exact)):return ''
            return self._multi_event_packet(scope,query,exact,budget,cutoff=request_time,
                                            current_message_id=current_message_id,data_version=data_version)
        cutoff=request_time.astimezone(timezone.utc).isoformat()
        excluded=set(exclude_ids or ())
        eligible=[event for event in events if event['id'] not in excluded]
        details=self._weak_candidate_details(scope,{'content':query,'recorded_at':cutoff},eligible) if self.weak_association_enabled else []
        ids=[x['event_id'] for x in details]
        method='本地词组弱匹配'
        semantic_incomplete=False
        if self.association_context_enabled and len(details)==1:
            item=details[0]
            source_ids={c['source_id'] for c in item['clues'] if c['source_id'] and len(c['text'])>=4}
            # 同一原话上的两个不重叠词组也可作为弱线索；不能跨原话拼凑。
            if not re.search(r'不是|并非|不指|别说|不对|记错',query):
                snap=self.flow._snapshot(scope,item['event_id'])
                terms={c['text'] for c in item['clues'] if c['source_id']}
                if snap and not snap['stale']:
                    source_ids.update(row['id'] for row in snap['rows']
                                      if sum(term in row['content'] for term in terms)>=2)
            event=next((e for e in events if e['id']==item['event_id']),None)
            if source_ids and event:
                wrapper={'未确认的联想事件':event['title'],
                         '说明':'以下历史原话可能相关；指代尚未确认，不要断言是同一事件',
                         '关联历史':None}
                overhead=len(json.dumps(wrapper,ensure_ascii=False,separators=(',',':')))-len('null')
                packet=self._context_event(scope,event,query,budget-overhead,focus_ids=source_ids,
                                           cutoff=request_time,current_message_id=current_message_id)
                if packet:
                    wrapper['关联历史']=json.loads(packet)
                    output=json.dumps(wrapper,ensure_ascii=False,separators=(',',':'))
                    if len(output)<=budget:return output
        if not ids and self.model_dir:
            # Avoid withholding a successful local result while a BGE load blocks.
            try:
                semantic_ids=self._index().search(scope,query)
                semantic_incomplete=bool(self.index.last_sync.get('unavailable'))
            except Exception:semantic_ids=[]
            allowed={e['id']:e for e in events};limit=datetime.fromisoformat(cutoff)
            denied=self._negated_events(scope,query,events)
            for eid in semantic_ids:
                event=allowed.get(eid)
                if not event or eid in denied or eid in excluded:continue
                snap=self.flow._snapshot(scope,eid)
                if not snap or snap['stale']:continue
                try:
                    times=[datetime.fromisoformat(x['recorded_at']) for x in snap['rows']]
                    if any(t.tzinfo is None or t>=limit for t in times):continue
                except (TypeError,ValueError):continue
                ids.append(eid)
            method='本地语义相似'
            # 事件向量只是召回线索；仅在独立开关开启且唯一候选时尝试定位原话。
            if self.semantic_source_context_enabled and not semantic_incomplete and len(ids)==1 and not re.search(r'不是|并非|不指|别说|不对|记错|错了',query):
                event=allowed[ids[0]]
                try:
                    version=self.flow.db.execute('PRAGMA data_version').fetchone()[0]
                    snap=self.flow._snapshot(scope,event['id'])
                    indexed=self.flow.db.execute('SELECT fingerprint FROM e419_vectors WHERE scope=? AND event_id=? AND model_key=?',
                                                 (scope,event['id'],self.index.model_key)).fetchone()
                    if snap and not snap['stale'] and indexed and indexed['fingerprint']==snap['digest'] and 1<=len(snap['rows'])<=100:
                        rows=snap['rows'];encoder=self.embedder
                        questions=encoder.encode_queries([query])
                        if not isinstance(questions,(list,tuple)) or len(questions)!=1:
                            raise ValueError('embedding shape')
                        q=_unit(questions[0]);rank=[];cached=self.semantic_source_vectors
                        if getattr(encoder,'model_key',None)!=self.index.encoder_key:raise ValueError('encoder changed')
                        # 每批最多 20 条，必须完成全部批次，不能把部分候选当作唯一。
                        for start in range(0,len(rows),20):
                            batch=rows[start:start+20]
                            keys=[(scope,self.index.model_key,r['id'],hashlib.sha256(r['content'].encode('utf-8')).hexdigest()) for r in batch]
                            missing=[(r,k) for r,k in zip(batch,keys) if k not in cached]
                            if missing:
                                docs=encoder.encode_documents([r['content'] for r,k in missing])
                                if not isinstance(docs,(list,tuple)) or len(docs)!=len(missing):
                                    raise ValueError('embedding shape')
                                prepared=[_unit(vector) for vector in docs]
                                if any(len(vector)!=len(q) for vector in prepared):raise ValueError('embedding dimension')
                                for (_,key),vector in zip(missing,prepared):cached[key]=vector
                            for row,key in zip(batch,keys):
                                v=cached[key];cached.move_to_end(key)
                                if len(v)!=len(q):raise ValueError('embedding dimension')
                                rank.append((sum(a*b for a,b in zip(q,v)),row['id']))
                            while len(cached)>100:cached.popitem(last=False)
                        rank.sort(key=lambda x:(-x[0],x[1]))
                        # 门槛不是概率或真实性：两个来源接近时不选。
                        if rank[0][0]>=self.source_min_cosine and (len(rank)==1 or rank[0][0]-rank[1][0]>=self.source_min_lead):
                            wrapper={'未确认的联想事件':event['title'],'匹配方式':'本地语义相似',
                                     '说明':'语义相似仅是线索，历史原话可能无关，指代尚未确认', '关联历史':None}
                            overhead=len(json.dumps(wrapper,ensure_ascii=False,separators=(',',':')))-len('null')
                            packet=self._context_event(scope,event,query,budget-overhead,focus_ids={rank[0][1]},
                                                       cutoff=request_time,current_message_id=current_message_id)
                            latest=self.flow._snapshot(scope,event['id'])
                            unchanged=(latest and not latest['stale'] and latest['digest']==snap['digest'] and
                                       self.flow.db.execute('PRAGMA data_version').fetchone()[0]==version)
                            if packet and unchanged:
                                wrapper['关联历史']=json.loads(packet)
                                output=json.dumps(wrapper,ensure_ascii=False,separators=(',',':'))
                                if len(output)<=budget:return output
                except Exception:
                    while len(self.semantic_source_vectors)>100:self.semantic_source_vectors.popitem(last=False)
                    pass  # 模型不可用或来源不满足条件时仅退回事件候选。
        mapping={e['id']:e['title'] for e in events}
        titles=[mapping[eid] for eid in ids if eid in mapping]
        if not titles:
            if self.automatic_memory_enabled and self.model_dir:
                from ..auto_cross_update import cross_update_context
                blocked,paired=cross_update_context(self.flow.db,self._index().model_key,self._index().embedder,
                    scope,query,cutoff,budget,current_message_id,excluded)
                if blocked:return paired
            automatic=self._auto_topic_context(scope,query,cutoff,budget,current_message_id,excluded)
            if not automatic:automatic=self._auto_group_context(scope,query,cutoff,budget,current_message_id,excluded)
            pending=automatic or self._pending_context(scope,query,cutoff,budget,current_message_id,excluded)
            relevant=pending or (self._semantic_unlinked_context(scope,query,cutoff,budget,current_message_id,excluded)
                                 if self.automatic_memory_enabled else '')
            return relevant or self._background_context(
                scope,query,min(budget,320),current_message_id,cutoff)
        payload={'未确认的联想候选':titles,'匹配方式':method,'说明':'候选可能不相关，不是已确认记忆'}
        if method=='本地语义相似' and semantic_incomplete:
            payload['索引提示']='部分事件尚未完成编码，候选不完整，不提供语义原话'
        if method=='本地词组弱匹配' and (self.event_priority_enabled or self.recency_rank_enabled or self.preference_rank_enabled):
            payload['排序提示']='候选顺序可能受人工标记、近期记录或偏好字面线索影响'
        out=json.dumps(payload,ensure_ascii=False)
        return out if len(out)<=budget else ''
