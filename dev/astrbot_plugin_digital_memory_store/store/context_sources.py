"""Store 的 事件供给：事件准备、语义分组、主题/向量同步（mixin，由 ContextMixin 组合）。"""
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



class ContextSourceMixin:
    def _context_event(self,scope,event,question,budget,*,focus_ids=None,cutoff=None,current_message_id=None,minimum_only=False):
        plan=self._prepare_context_event(scope,event,question,focus_ids=focus_ids,cutoff=cutoff,
                                         current_message_id=current_message_id)
        return plan(budget,minimum_only) if plan else ''

    def _prepare_context_event(self,scope,event,question,*,focus_ids=None,cutoff=None,current_message_id=None):
        """单次请求内准备来源、关系与时间边界，返回可重复分配预算的临时函数。"""
        snap=self.flow._snapshot(scope,event['id'])
        if not snap or snap['stale'] or len(snap['rows'])>1000:return None
        # 当前消息或将来的记录不能作为已发生的历史；关系组也不能被拆开。
        if cutoff is None:cutoff=datetime.now(timezone.utc)
        try:
            if cutoff.utcoffset() is None:return None
            if any((datetime.fromisoformat(r['recorded_at']).utcoffset() is None or
                    datetime.fromisoformat(r['recorded_at'])>=cutoff) for r in snap['rows']):return None
        except (TypeError,ValueError,OverflowError):return None
        if isinstance(current_message_id,str) and 1<=len(current_message_id)<=256:
            event_hash=hashlib.sha256((scope+'\0'+current_message_id).encode('utf-8')).hexdigest()
            source=self.flow.writer.status(scope,'captured:'+event_hash)
            if source and any(r['id']==source['message_id'] for r in snap['rows']):return None
        pending=self.flow.db.execute("SELECT 1 FROM e417_ingest WHERE scope=? AND candidate_event_id=? AND state='pending_review' LIMIT 1",(scope,event['id'])).fetchone()
        if pending:return None
        rows=snap['rows'];relations=self.flow._event_rows(scope,event['id'])[1]
        pending_hint=self._pending_update_hint(scope,event['title'],snapshot=snap)
        try:linked_update_hint=bool(self._unresolved_linked_updates(scope,snap,relations))
        except (TypeError,ValueError,OverflowError):return None
        by_id={r['id']:r for r in rows};parent={k:k for k in by_id}
        def root(x):
            while parent[x]!=x:
                parent[x]=parent[parent[x]];x=parent[x]
            return x
        for edge in relations:
            if edge['newer'] not in parent or edge['older'] not in parent:return None
            a,b=root(edge['newer']),root(edge['older'])
            if a!=b:parent[a]=b
        groups={}
        for row in rows:groups.setdefault(root(row['id']),[]).append(row)
        earliest=bool(re.search(r'最初|最早|起初|原本|一开始|原计划',question))
        ordered=sorted(groups.values(),key=lambda g:(g[0]['recorded_at'],g[0]['id']) if earliest else
                       (g[-1]['recorded_at'],g[-1]['id']),reverse=not earliest)
        detail_ranked=False
        if self.detail_priority_enabled and not focus_ids and not earliest and not re.search(r'不是|并非|不对|记错|错了|别说|不指',question):
            # 去除事件名称与别名后，只把问题中有辨识度的三字重合用于组级排序。
            detail=question.replace(event['title'],' ')
            for alias in self.list_aliases(scope,event['title']):detail=detail.replace(alias,' ')
            generic={'怎么样','是什么','有没有','多少钱','什么时候','为什么','如何','哪里有','是不是','记不记','还记得'}
            grams={detail[i:i+3] for i in range(len(detail)-2)
                   if all('\u4e00'<=c<='\u9fff' for c in detail[i:i+3])
                   and detail[i:i+3] not in generic and len(self._character_pairs(detail[i:i+3]))>=2}
            group_hits=[]
            for group in ordered:
                hits={term for term in grams if any(term in row['content'] for row in group)}
                group_hits.append(hits)
            frequencies=Counter(term for hits in group_hits for term in hits)
            scores={id(group):sum(1.0/frequencies[term] for term in hits)
                    for group,hits in zip(ordered,group_hits)}
            if any(scores.values()):
                ordered.sort(key=lambda group:-scores[id(group)])
                detail_ranked=True
        if focus_ids:
            # Stable sort keeps the question's temporal order inside each tier.
            ordered.sort(key=lambda g:not any(r['id'] in focus_ids for r in g))
        selected_dates={};target_date=None
        if self.occurred_date_context_enabled:
            # 仅支持有完整年月日的查询；无法判定时不把其他日期当作答案。
            dates=list(re.finditer(r'(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?!\d)',question))
            prefixes=list(re.finditer(r'(?<!\d)\d{4}[-/年]\d{1,2}',question))
            if prefixes:
                if len(dates)!=len(prefixes) or re.search(r'不是|并非|不对|记错|错了|别说|不指',question):return None
                try:targets={date(*(int(part) for part in match.groups())).isoformat() for match in dates}
                except ValueError:return None
                if len(targets)!=1:return None
                target_date=next(iter(targets))
                selected_dates=self._event_annotated_dates(scope,snap,cutoff)
                ordered=[g for g in ordered if any(selected_dates.get(r['id'])==target_date for r in g)]
                if focus_ids:ordered=[g for g in ordered if any(r['id'] in focus_ids for r in g)]
                if not ordered:return None
        kinds={'corrects':'修正','supersedes':'替代','related':'相关','retracts':'撤回'}
        imported_ids={r['linked_source'] for r in self.flow.db.execute('''SELECT c.linked_source FROM dl_capture c
            JOIN dl_import_provenance p ON p.scope=c.scope AND p.seq=c.seq
            JOIN e417_phase ph ON ph.scope=c.scope AND ph.message_id=c.linked_source
            WHERE c.scope=? AND ph.event_id=?''',(scope,event['id']))}
        def plan(budget,minimum_only=False):
            def render(selected,compact=False):
                ids={r['id'] for r in selected}
                chronological=[r for r in rows if r['id'] in ids]
                index={r['id']:i+1 for i,r in enumerate(chronological)}
                payload={'事件':event['title'],'说明':'用户历史原话及人工关系，不等于已证实现况',
                         '覆盖':f'{len(chronological)}/{len(rows)}条，可能遗漏背景',
                         '原话':[{'编号':index[r['id']],'记录时间':r['recorded_at'],'内容':r['content']} for r in chronological],
                         '关系':[{'较新':index[x['newer']],'类型':kinds.get(x['kind'],'未知'),'较旧':index[x['older']]}
                               for x in relations if x['newer'] in ids and x['older'] in ids]}
                if compact:
                    # 精简字段名称，保留每条完整原话、记录时间及全部组内关系。
                    payload['说明']='原话项=[记录时间,全文]，序号从1起；关系项=[新序号,类型,旧序号]。历史陈述不等于现况'
                    payload['原话']=[[r['recorded_at'],r['content']] for r in chronological]
                    payload['关系']=[[index[x['newer']],kinds.get(x['kind'],'未知'),index[x['older']]]
                                   for x in relations if x['newer'] in ids and x['older'] in ids]
                imported_numbers=[index[r['id']] for r in chronological if r['id'] in imported_ids]
                if imported_numbers:payload['导入时间说明']={'原话序号':imported_numbers,'说明':'记录时间由导入文件提供，未经独立核验'}
                if target_date:
                    payload['日期标注']={'说明':'日期由人工标注，不等于客观事实；其他记录日期未提供',
                                     '条目':[[index[r['id']],selected_dates[r['id']]] for r in chronological if r['id'] in selected_dates]}
                if detail_ranked:payload['选择依据']='按问题与原文的字面重合优先，可能漏掉相关背景'
                if linked_update_hint:payload['关系提醒']='已关联原话中可能有更新尚未核对，旧内容不一定反映现况'
                if pending_hint:payload['待审提醒']='有可能相关的新消息尚未审核，旧记录不一定反映现况'
                return json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            def choose(compact):
                selected=[]
                for position,group in enumerate(ordered):
                    proposal=selected+group
                    if len(render(proposal,compact))<=budget:
                        selected=proposal
                        if minimum_only:break
                    elif position==0:return [],''  # 不以其他记录冒充优先组。
                return (selected,render(selected,compact)) if selected else ([], '')
            selected,output=choose(False)
            if self.compact_context_enabled:
                compact_selected,compact_output=choose(True)
                if (len(compact_selected)>len(selected) or
                    (minimum_only and compact_output and (not output or len(compact_output)<len(output)))):
                    selected,output=compact_selected,compact_output
            # 最小预留只包含第一优先关系组，不使用摘要替代原话。
            if minimum_only:return output
            # 默认依旧优先原话。显式开启概览优先时，只有未覆盖全部原话才比较摘要。
            if output and (not self.brief_overview_priority_enabled or len(selected)==len(rows)):return output
            # 仅在原话无法放入时考虑摘要；明确指向事件，且没有检测到可能的更新。
            if (not self.reviewed_brief_context_enabled or focus_ids or target_date or pending_hint or linked_update_hint or
                re.search(r'最初|最早|起初|原本|以前|之前|后来|现在|最近|过去|历史|当时|何时|时候|哪天|几月|几号|不是|并非|不对|记错|错了|\d{4}',question)):
                return output
            names=self._named_events(scope,question,[event])
            if not names:return output
            # 摘要没有细节完整性保证，只供事件整体的有限提问。
            remainder=question.replace(event['title'],'')
            for alias in self.list_aliases(scope,event['title']):remainder=remainder.replace(alias,'')
            remainder=re.sub(r'[\s，,。！？?!：:]+','',remainder)
            if not re.fullmatch(r'(?:|是什么|怎么样|怎样|如何|的情况|什么情况|说过什么|聊过什么|介绍一下|讲讲|帮我回忆一下)',remainder):return output
            try:
                summary=self.read_source_brief(scope,event['title'])
                created=datetime.fromisoformat(summary['created_at']) if summary else None
                if not summary or not summary['current'] or created.utcoffset() is None or created>=cutoff:return output
            except (ValueError,TypeError,OverflowError):return output
            origin={'human_written':'人工编写','model_reviewed':'模型生成、人工确认',
                    'legacy_unknown':'旧摘要，生成方式未记录'}.get(summary['origin'],'生成方式未知')
            field='人工摘要' if summary['origin']=='human_written' else '摘要'
            packet={'事件':event['title'],'说明':origin+'摘要，可能遗漏或误解，并非完整历史或客观事实',
                    field:summary['brief'],'依据摘录':summary['excerpt'],
                    '依据记录时间':summary['source_time'],'覆盖':'未包含完整历史，请勿推断遗漏内容'}
            if self.flow.db.execute('''SELECT 1 FROM dl_capture c JOIN dl_import_provenance p
                ON p.scope=c.scope AND p.seq=c.seq WHERE c.scope=? AND c.linked_source=? LIMIT 1''',
                (scope,summary['source_id'])).fetchone():
                packet['依据时间来源']='来自导入文件，未经独立核验'
            result=json.dumps(packet,ensure_ascii=False,separators=(',',':'))
            return result if len(result)<=budget and (not output or len(result)<len(output)) else output

        return plan

    def _semantic_auto_groups(self,scope,query,current_hash,excluded,cutoff,after_gap):
        """对近期自动组完整来源编码；有错误则放弃整次语义选择。"""
        if not self.auto_group_semantic_enabled or not self.model_dir:return {}
        db=self.flow.db
        try:
            limit=datetime.fromisoformat(cutoff)
            boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if limit.utcoffset() is None or (boundary and boundary.utcoffset() is None):return {}
            from ..auto_organize import read_group
            idx=self._index();encoder=idx.embedder
            if getattr(encoder,'model_key',None)!=idx.encoder_key:return {}
            if self.automatic_memory_enabled:
                self.sync_auto_source_vectors(scope,limit=20)
                missing=db.execute('''SELECT 1 FROM dl_capture c WHERE c.scope=? AND c.state='pending'
                    AND c.event_hash<>? AND NOT EXISTS (SELECT 1 FROM dl_auto_source_vectors v
                    WHERE v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha) LIMIT 1''',
                    (scope,current_hash,idx.model_key)).fetchone()
                if missing:return {}
            groups={}
            sql="SELECT seq FROM dl_capture WHERE scope=? AND state='pending' ORDER BY seq DESC"
            if not self.automatic_memory_enabled:sql+=' LIMIT 60'
            for row in db.execute(sql,(scope,)).fetchall():
                group=read_group(db,scope,row['seq'])
                if not group:continue
                key=tuple(group['members'])
                if key in groups:continue
                texts=[];valid=True
                for seq in key:
                    source=db.execute('SELECT * FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
                    if not source or 'pending:'+str(seq) in excluded or source['event_hash']==current_hash:
                        valid=False;break
                    stamp=datetime.fromisoformat(source['recorded_at'])
                    if stamp.utcoffset() is None or stamp>=limit or (boundary and stamp<=boundary):valid=False;break
                    if hashlib.sha256(source['content'].encode()).hexdigest()!=source['content_sha']:valid=False;break
                    texts.append(source['content'])
                if valid:groups[key]=(group,texts)
                if len(groups)>1000:return {}
            if not groups:return {}
            texts=[];owners=[]
            for key,(_,sources) in groups.items():
                for source in sources:texts.append(source);owners.append(key)
            if not self.automatic_memory_enabled and len(texts)>100:return {}
            query_vectors=encoder.encode_queries([query])
            if not isinstance(query_vectors,(list,tuple)) or len(query_vectors)!=1:return {}
            q=_unit(query_vectors[0]);scores={}
            if self.automatic_memory_enabled:
                for key in groups:
                    for seq in key:
                        vector=db.execute('''SELECT v.vector_json FROM dl_auto_source_vectors v JOIN dl_capture c
                            ON c.scope=v.scope AND c.seq=v.seq AND c.content_sha=v.content_sha
                            WHERE v.scope=? AND v.seq=? AND v.model_key=?''',(scope,seq,idx.model_key)).fetchone()
                        if not vector:return {}
                        v=_unit(json.loads(vector['vector_json']))
                        if len(v)!=len(q):return {}
                        score=sum(a*b for a,b in zip(q,v));scores[key]=max(scores.get(key,-1.0),score)
            else:
                for start in range(0,len(texts),20):
                    batch=texts[start:start+20];vectors=encoder.encode_documents(batch)
                    if not isinstance(vectors,(list,tuple)) or len(vectors)!=len(batch):return {}
                    for key,vector in zip(owners[start:start+20],vectors):
                        v=_unit(vector)
                        if len(v)!=len(q):return {}
                        score=sum(a*b for a,b in zip(q,v));scores[key]=max(scores.get(key,-1.0),score)
            ranked=sorted(scores.items(),key=lambda x:(-x[1],x[0]))
            if ranked[0][1]<self.source_min_cosine or (len(ranked)>1 and ranked[0][1]-ranked[1][1]<self.source_min_lead):return {}
            key=ranked[0][0]
            # 编码期间可能有其他数据库连接改变来源或关联。
            if not read_group(db,scope,key[0]) or tuple(read_group(db,scope,key[0])['members'])!=key:return {}
            return {key:groups[key][0]}
        except Exception:return {}

    def _auto_source_conflict(self,scope,seq):
        if not self.automatic_memory_enabled or not self.model_dir:return False
        try:
            from ..auto_topology import source_has_conflict
            return source_has_conflict(self.flow.db,scope,self._index().model_key,seq)
        except Exception:return True

    def _auto_group_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        """仅提供唯一来源完好的临时分组；不把自动关联当作确认事实。"""
        if (not self.auto_group_context_enabled or not isinstance(current_message_id,str) or
            not 1<=len(current_message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query)):
            return ''
        terms={query[i:i+4] for i in range(len(query)-3)
               if all('\u4e00'<=c<='\u9fff' for c in query[i:i+4])}
        if not terms and not self.auto_group_semantic_enabled:return ''
        db=self.flow.db;version=db.execute('PRAGMA data_version').fetchone()[0]
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode()).hexdigest()
        # 较早积压可能改变分组；当前消息自身尚未处理不阻塞。
        if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
            WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return ''
        from ..auto_organize import read_group
        matches={}
        for seq in self.pending_index.candidates(scope,self._character_pairs(query)):
            row=db.execute('SELECT content FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
            if not row or not any(t in row['content'] for t in terms):continue
            group=read_group(db,scope,seq)
            if group:matches[tuple(group['members'])]=group
            if len(matches)>1:return ''
        matching='字面重合'
        if not matches:
            matches=self._semantic_auto_groups(scope,query,current_hash,excluded,cutoff,after_gap)
            matching='本地语义相似'
        if len(matches)!=1:return ''
        members=next(iter(matches));limit=datetime.fromisoformat(cutoff);items=[]
        try:
            boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if boundary is not None and boundary.utcoffset() is None:return ''
        except (TypeError,ValueError,OverflowError):return ''
        for seq in members:
            if 'pending:'+str(seq) in excluded:return ''
            if self._auto_source_conflict(scope,seq):return ''
            row=db.execute('''SELECT c.*,p.seq AS imported FROM dl_capture c LEFT JOIN dl_import_provenance p
                ON p.scope=c.scope AND p.seq=c.seq WHERE c.scope=? AND c.seq=?''',(scope,seq)).fetchone()
            if not row or row['event_hash']==current_hash or row['state']!='pending':return ''
            if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']:return ''
            try:
                timestamp=datetime.fromisoformat(row['recorded_at'])
                if timestamp.utcoffset() is None or timestamp>=limit or (boundary and timestamp<=boundary):return ''
            except (TypeError,ValueError,OverflowError):return ''
            item={'序号':seq,'原话':row['content'],'记录时间':row['recorded_at']}
            if row['imported'] is not None:item['时间来源']='导入文件，未经独立核验'
            items.append(item)
        basis=next(iter(matches.values()))['basis']
        payload={'待审记录序号':members[0],'待审相关序号':list(members),'可能相关原话':items,
                 '线索方式':basis,'定位方式':matching,
                 '说明':'系统自动关联的未确认线索，事件归属及新旧关系均不确定'}
        relation=next(iter(matches.values())).get('relation_hint')
        if relation:payload['关系线索']=relation+'（模型推测，未确认）'
        if self.automatic_memory_enabled and self.model_dir:
            from ..auto_model import unresolved_hint_sources
            if unresolved_hint_sources(db,scope,self._index().model_key,members):
                payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
        if boundary:payload['采集情况']='先前存在已检测到的采集缺口，历史可能不完整；缺失内容无法恢复'
        output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
        return output if len(output)<=budget else ''

    def sync_auto_topics(self,limit=20):
        if not self.automatic_memory_enabled or not self.model_dir:return 0
        from ..auto_topics import sync_topics,reconcile_topics
        from ..auto_topology import recover_model_merges,rebuild_conflicted_topics
        model_key=self._index().model_key
        recover_model_merges(self.flow.db,model_key)
        rebuild_conflicted_topics(self.flow.db,model_key)
        count=sync_topics(self.flow.db,model_key,limit)
        reconcile_topics(self.flow.db,model_key)
        return count

    def _auto_topic_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        if not self.automatic_memory_enabled or not self.model_dir:return ''
        from ..auto_topics import topic_context
        try:
            idx=self._index()
            if getattr(idx.embedder,'model_key',None)!=idx.encoder_key:return ''
            return topic_context(self.flow.db,idx.model_key,idx.embedder,scope,query,cutoff,budget,current_message_id,excluded,after_gap)
        except Exception:return ''

    def sync_auto_source_vectors(self,scope=None,limit=20):
        """增量编码已保存原话；失败记录退避，不更改原话或确认状态。"""
        if not self.automatic_memory_enabled or not self.model_dir:return 0
        idx=self._index();encoder=idx.embedder;db=self.flow.db
        if getattr(encoder,'model_key',None)!=idx.encoder_key:return 0
        now=datetime.now(timezone.utc);stamp=now.isoformat(timespec='seconds')
        rows=db.execute('''SELECT c.scope,c.seq,c.content,c.content_sha FROM dl_capture c
            LEFT JOIN dl_auto_source_vectors v ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha
            LEFT JOIN dl_auto_vector_failures f ON f.scope=c.scope AND f.seq=c.seq AND f.model_key=? AND f.content_sha=c.content_sha
            WHERE c.state='pending' AND (? IS NULL OR c.scope=?) AND v.seq IS NULL
                AND (f.seq IS NULL OR f.next_at<=?) ORDER BY c.seq LIMIT ?''',
            (idx.model_key,idx.model_key,scope,scope,stamp,limit)).fetchall()
        completed=0
        for start in range(0,len(rows),20):
            batch=rows[start:start+20];valid=[];outcomes={};chunk_counts={}
            for row in batch:
                try:
                    if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']:raise ValueError('source changed')
                    valid.append(row)
                except (AttributeError,TypeError,ValueError):outcomes[(row['scope'],row['seq'])]=None
            if valid:
                try:
                    encoded=encoder.encode_documents([r['content'] for r in valid])
                    if not isinstance(encoded,(list,tuple)) or len(encoded)!=len(valid):raise ValueError('shape')
                except Exception:
                    # 一条超长或损坏原话不能使同批其他消息失去索引。
                    encoded=[]
                    for row in valid:
                        try:
                            one=encoder.encode_documents([row['content']])
                            encoded.append(one[0] if isinstance(one,(list,tuple)) and len(one)==1 else None)
                        except ValueError:
                            try:
                                if len(row['content'])<=128:raise ValueError('not a long source')
                                from ..auto_vector_chunks import encode_complete_source
                                vector,count=encode_complete_source(encoder,row['content'])
                                encoded.append(vector);chunk_counts[(row['scope'],row['seq'])]=count
                            except Exception:encoded.append(None)
                        except Exception:encoded.append(None)
                for row,raw in zip(valid,encoded):
                    try:outcomes[(row['scope'],row['seq'])]=_unit(raw)
                    except Exception:outcomes[(row['scope'],row['seq'])]=None
            with db:
                for row in batch:
                    current=db.execute('SELECT content,content_sha,state FROM dl_capture WHERE scope=? AND seq=?',(row['scope'],row['seq'])).fetchone()
                    if not current or current['state']!='pending' or current['content']!=row['content'] or current['content_sha']!=row['content_sha']:continue
                    vector=outcomes[(row['scope'],row['seq'])]
                    if vector is not None:
                        db.execute('INSERT OR REPLACE INTO dl_auto_source_vectors VALUES(?,?,?,?,?)',
                                   (row['scope'],row['seq'],idx.model_key,row['content_sha'],json.dumps(vector,separators=(',',':'))))
                        count=chunk_counts.get((row['scope'],row['seq']))
                        if count:
                            db.execute('INSERT OR REPLACE INTO dl_auto_vector_chunks VALUES(?,?,?,?,?)',
                                       (row['scope'],row['seq'],idx.model_key,row['content_sha'],count))
                        else:db.execute('DELETE FROM dl_auto_vector_chunks WHERE scope=? AND seq=? AND model_key=?',
                                        (row['scope'],row['seq'],idx.model_key))
                        db.execute('DELETE FROM dl_auto_vector_failures WHERE scope=? AND seq=? AND model_key=?',(row['scope'],row['seq'],idx.model_key))
                        completed+=1
                    else:
                        failure=db.execute('SELECT attempts FROM dl_auto_vector_failures WHERE scope=? AND seq=? AND model_key=?',
                                           (row['scope'],row['seq'],idx.model_key)).fetchone()
                        attempts=(failure['attempts'] if failure else 0)+1
                        retry=(now+timedelta(seconds=min(300,2**min(attempts,8)))).isoformat(timespec='seconds')
                        db.execute('INSERT OR REPLACE INTO dl_auto_vector_failures VALUES(?,?,?,?,?,?)',
                                   (row['scope'],row['seq'],idx.model_key,row['content_sha'],attempts,retry))
        return completed

    def _semantic_unlinked_context(self,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
        """自动模式下让单条未归组原话也能参与本地语义召回。"""
        if (not self.automatic_memory_enabled or not self.model_dir or not isinstance(current_message_id,str) or
            not 1<=len(current_message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query)):return ''
        db=self.flow.db;version=db.execute('PRAGMA data_version').fetchone()[0]
        current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode()).hexdigest()
        if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
            WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return ''
        try:
            limit=datetime.fromisoformat(cutoff);boundary=datetime.fromisoformat(after_gap) if after_gap else None
            if limit.utcoffset() is None or (boundary and boundary.utcoffset() is None):return ''
            from ..auto_organize import read_group
            idx=self._index();encoder=idx.embedder
            if getattr(encoder,'model_key',None)!=idx.encoder_key:return ''
            # 小规模新库可以立即建立索引；大量历史由后台分批补齐。
            self.sync_auto_source_vectors(scope,limit=20)
            missing=db.execute('''SELECT 1 FROM dl_capture c WHERE c.scope=? AND c.state='pending'
                AND c.event_hash<>? AND NOT EXISTS (SELECT 1 FROM dl_auto_source_vectors v
                WHERE v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha) LIMIT 1''',
                (scope,current_hash,idx.model_key)).fetchone()
            if missing:return ''
            # 只保留得分最高的两个来源；仍检查全部合格候选以保留歧义保护。
            best=None;second=None;q=None
            for row in db.execute('''WITH ordered AS (
                SELECT c.*,LEAD(content) OVER (ORDER BY seq) AS next_content,
                    LEAD(recorded_at) OVER (ORDER BY seq) AS next_time
                FROM dl_capture c WHERE c.scope=?
                ) SELECT c.*,v.vector_json,m.topic_id AS auto_topic,
                    (g.seq IS NOT NULL OR ch.seq IS NOT NULL OR p.seq IS NOT NULL) AS may_have_group,
                    EXISTS(SELECT 1 FROM dl_auto_topic_members n WHERE n.scope=m.scope
                        AND n.topic_id=m.topic_id AND n.model_key=m.model_key AND n.seq<>m.seq) AS has_topic_peer
                FROM ordered c JOIN dl_auto_source_vectors v
                ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha
                LEFT JOIN dl_auto_topic_members m ON m.scope=c.scope AND m.seq=c.seq AND m.model_key=v.model_key
                LEFT JOIN dl_auto_groups g ON g.scope=c.scope AND g.seq=c.seq
                LEFT JOIN dl_auto_chains ch ON ch.scope=c.scope AND ch.seq=c.seq
                LEFT JOIN dl_auto_model_pairs p ON p.scope=c.scope AND p.seq=c.seq
                WHERE c.state='pending' ORDER BY c.seq''',(scope,idx.model_key)):
                if ('pending:'+str(row['seq']) in excluded or row['event_hash']==current_hash or
                    row['content'].strip()==query.strip() or _UPDATE_CUES.search(row['content'])):continue
                # 只有存在分组记录时才执行完整来源校验，失效分组仍可回退单条召回。
                if row['may_have_group'] and read_group(db,scope,row['seq']):continue
                if row['has_topic_peer']:continue
                # 没有事件归属时不可能存在事件内冲突；有归属的单条仍需校验。
                if row['auto_topic'] is not None and self._auto_source_conflict(scope,row['seq']):continue
                if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']:continue
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None or stamp>=limit or (boundary and stamp<=boundary):continue
                if q is None:
                    vectors=encoder.encode_queries([query])
                    if not isinstance(vectors,(list,tuple)) or len(vectors)!=1:return ''
                    q=_unit(vectors[0])
                v=_unit(json.loads(row['vector_json']))
                if len(v)!=len(q):return ''
                item=(sum(a*b for a,b in zip(q,v)),row)
                if best is None or item[0]>best[0]:second=best;best=item
                elif second is None or item[0]>second[0]:second=item
            if best is None or best[0]<self.source_min_cosine or (second is not None and best[0]-second[0]<self.source_min_lead):return ''
            row=best[1]
            payload={'待审记录序号':row['seq'],'未整理历史原话':row['content'],'记录时间':row['recorded_at'],
                     '定位方式':'本地语义相似','说明':'自动选择的历史原话，仅是未确认线索，可能无关或过时'}
            peer=self._possible_update_peer(scope,row)
            if peer:
                payload['后续可能更新']={'序号':peer['seq'],'原话':peer['content'],'记录时间':peer['recorded_at']}
                payload['说明']='自动选择的历史原话，其后一条可能是更新；新旧关系与当前状态均未确认'
            from ..auto_vector_chunks import add_chunk_notice
            add_chunk_notice(db,scope,idx.model_key,[row['seq']],payload)
            from ..auto_model import unresolved_hint_sources
            if unresolved_hint_sources(db,scope,idx.model_key,{row['seq']}):
                payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
            if db.execute('SELECT 1 FROM dl_import_provenance WHERE scope=? AND seq=?',(scope,row['seq'])).fetchone():
                payload['时间来源']='导入文件，未经独立核验'
            if boundary:payload['采集情况']='先前存在已检测到的采集缺口，历史可能不完整；缺失内容无法恢复'
            output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            if db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
            return output if len(output)<=budget else ''
        except Exception:return ''
