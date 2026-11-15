"""在未确认归属的不同推测事件之间，保守供给可能变化的原话。"""
import hashlib
import json
import re
from datetime import datetime
from .auto_model import _fourgrams,separated,unresolved_hint_sources,read_relation_hint
from .auto_topology import source_has_conflict
from .engine.e419.dynamic_index import _unit

_CHANGE_WORDS=('改成','改为','改到','调整为','推迟','提前','取消','不去了','不去','换成','更正','说错','纠正','不再')
_CHANGE=re.compile('|'.join(map(re.escape,_CHANGE_WORDS)))


def sync_text_index(db,limit=100):
    """游标补历史、脏队列处理变化；每批和游标进度同事务提交。"""
    if not isinstance(limit,int) or isinstance(limit,bool) or limit<1:return 0
    db.execute('BEGIN IMMEDIATE')
    with db:
        last=db.execute('SELECT last_seq FROM dl_auto_text_backfill WHERE id=1').fetchone()[0]
        # 给已处理历史的变更保留额度，避免旧记录重建被持续新消息饿死。
        dirty_limit=min(20,limit//4)
        dirty=db.execute('''SELECT c.* FROM dl_auto_text_dirty d JOIN dl_capture c ON c.scope=d.scope AND c.seq=d.seq
            WHERE d.seq<=? ORDER BY d.seq LIMIT ?''',(last,dirty_limit)).fetchall()
        rows=db.execute('SELECT * FROM dl_capture WHERE seq>? ORDER BY seq LIMIT ?',
                        (last,limit-len(dirty))).fetchall()
        if len(dirty)+len(rows)<limit:
            dirty=db.execute('''SELECT c.* FROM dl_auto_text_dirty d JOIN dl_capture c ON c.scope=d.scope AND c.seq=d.seq
                WHERE d.seq<=? ORDER BY d.seq LIMIT ?''',(last,limit-len(rows))).fetchall()
        done=0
        for r in [*dirty,*rows]:
            if r['state']!='pending':
                db.execute('DELETE FROM dl_auto_text_index WHERE scope=? AND seq=?',(r['scope'],r['seq']))
                db.execute('DELETE FROM dl_auto_text_grams WHERE scope=? AND seq=?',(r['scope'],r['seq']))
                db.execute('DELETE FROM dl_auto_text_dirty WHERE scope=? AND seq=?',(r['scope'],r['seq']))
                continue
            if not isinstance(r['content'],str) or hashlib.sha256(r['content'].encode()).hexdigest()!=r['content_sha']:
                db.execute('INSERT OR IGNORE INTO dl_auto_text_dirty VALUES(?,?)',(r['scope'],r['seq']));continue
            indexed=db.execute('SELECT 1 FROM dl_auto_text_index WHERE scope=? AND seq=? AND content_sha=?',
                               (r['scope'],r['seq'],r['content_sha'])).fetchone()
            if not indexed:
                db.execute('DELETE FROM dl_auto_text_grams WHERE scope=? AND seq=?',(r['scope'],r['seq']))
                db.executemany('INSERT INTO dl_auto_text_grams VALUES(?,?,?)',[(r['scope'],term,r['seq']) for term in _fourgrams(r['content'])])
                db.execute('INSERT OR REPLACE INTO dl_auto_text_index VALUES(?,?,?,?)',(r['scope'],r['seq'],r['content_sha'],int(bool(_CHANGE.search(r['content'])))))
                done+=1
            db.execute('DELETE FROM dl_auto_text_dirty WHERE scope=? AND seq=?',(r['scope'],r['seq']))
        if rows:db.execute('UPDATE dl_auto_text_backfill SET last_seq=? WHERE id=1',(rows[-1]['seq'],))
        return done


def _indexed_candidates(db,scope,current_hash):
    """完整索引才缩小候选，否则回退旧的全量检查。"""
    last=db.execute('SELECT last_seq FROM dl_auto_text_backfill WHERE id=1').fetchone()[0]
    maximum=db.execute('SELECT COALESCE(MAX(seq),0) FROM dl_capture').fetchone()[0]
    if last<maximum:return None
    dirty=db.execute('''SELECT 1 FROM dl_auto_text_dirty d JOIN dl_capture c ON c.scope=d.scope AND c.seq=d.seq
        WHERE d.scope=? AND c.state='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone()
    if dirty:return None
    changes=db.execute('''SELECT c.seq,c.content FROM dl_auto_text_index i JOIN dl_capture c ON c.scope=i.scope AND c.seq=i.seq
        WHERE i.scope=? AND i.is_change=1 AND c.state='pending' AND c.event_hash<>? ORDER BY c.seq''',(scope,current_hash)).fetchall()
    if not changes:return set()
    ids={r['seq'] for r in changes}
    for change in changes:
        terms=sorted(_fourgrams(change['content']))
        if not terms:continue
        # 长原话可能产生数千个不同片段，分批以兼容 SQLite 参数上限。
        for start in range(0,len(terms),400):
            chunk=terms[start:start+400];placeholders=','.join('?' for _ in chunk)
            for row in db.execute('SELECT DISTINCT seq FROM dl_auto_text_grams WHERE scope=? AND gram IN ('+placeholders+') AND seq<?',
                                  (scope,*chunk,change['seq'])):
                ids.add(row['seq'])
                if len(ids)>100000:raise ValueError('candidate count')
    return ids


def _model_cross_context(db,model_key,encoder,scope,query,cutoff_time,budget,current_hash,excluded):
    """按查询匹配有效的跨事件模型线索；不把模型判断当作确认事实。"""
    gap=db.execute('SELECT total,last_at FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
    boundary=datetime.fromisoformat(gap['last_at']) if gap and gap['total'] else None
    if boundary and boundary.utcoffset() is None:return True,''
    vector=None;edges=[]
    for hint in db.execute('SELECT older_seq,newer_seq,manifest_json FROM dl_auto_relation_hints WHERE scope=?',(scope,)):
        try:
            manifest=json.loads(hint['manifest_json'])
            if (not isinstance(manifest,list) or len(manifest)!=2 or not isinstance(manifest[0],dict) or
                manifest[0].get('mode')!='cross_update_search' or manifest[0].get('model_key')!=model_key):continue
            ids=(hint['older_seq'],hint['newer_seq'])
            relation=read_relation_hint(db,scope,*ids)
            if not relation:continue
            rows=[]
            for seq in ids:
                row=db.execute('''SELECT c.*,v.vector_json FROM dl_capture c JOIN dl_auto_source_vectors v
                    ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha
                    WHERE c.scope=? AND c.seq=? AND c.state='pending' ''',(model_key,scope,seq)).fetchone()
                if not row or row['event_hash']==current_hash:break
                stamp=datetime.fromisoformat(row['recorded_at'])
                if stamp.utcoffset() is None or stamp>=cutoff_time or (boundary and stamp<=boundary):break
                rows.append(row)
            if len(rows)!=2:continue
            if vector is None:
                encoded=encoder.encode_queries([query])
                if not isinstance(encoded,(list,tuple)) or len(encoded)!=1:return True,''
                vector=_unit(encoded[0])
            scores=[]
            for row in rows:
                v=_unit(json.loads(row['vector_json']))
                if len(v)!=len(vector):return True,''
                scores.append(sum(a*b for a,b in zip(vector,v)))
            edges.append((max(scores),rows,relation))
        except (ValueError,TypeError,KeyError,OverflowError):continue
    if not edges:return False,''
    # 共用来源的有效关系构成同一候选；无连接的事件仍作为独立候选竞争。
    parent={}
    def root(seq):
        parent.setdefault(seq,seq)
        while parent[seq]!=seq:
            parent[seq]=parent[parent[seq]];seq=parent[seq]
        return seq
    for _,rows,_ in edges:
        left,right=(root(row['seq']) for row in rows)
        if left!=right:parent[right]=left
    groups={}
    for score,rows,relation in edges:
        key=root(rows[0]['seq']);group=groups.setdefault(key,{'score':score,'rows':{},'relations':[]})
        group['score']=max(group['score'],score)
        for row in rows:group['rows'][row['seq']]=row
        group['relations'].append({'较早序号':rows[0]['seq'],'较新序号':rows[1]['seq'],'关系':relation+'（模型推测，未确认）'})
    ranked=sorted(groups.values(),key=lambda x:x['score'],reverse=True)
    if ranked[0]['score']<0.75:return False,''
    if len(ranked)>1 and ranked[0]['score']-ranked[1]['score']<0.10:return True,''
    best=ranked[0];ids=sorted(best['rows']);rows=[best['rows'][seq] for seq in ids]
    if len(ids)>budget//10 or any('pending:'+str(seq) in excluded for seq in ids):return True,''
    # 线索来源所属事件尚在冲突或合并回退时，不能绕过事件隔离直接提供。
    if any(source_has_conflict(db,scope,model_key,seq) for seq in ids):return True,''
    if any(separated(db,scope,a,b) for i,a in enumerate(ids) for b in ids[i+1:]):return True,''
    if any(datetime.fromisoformat(a['recorded_at'])>=datetime.fromisoformat(b['recorded_at']) for a,b in zip(rows,rows[1:])):return True,''
    data={'待审记录序号':ids[0],'待审相关序号':ids,
          '可能相关原话':[{'序号':r['seq'],'原话':r['content'],'记录时间':r['recorded_at']} for r in rows],
          '线索方式':'模型跨事件可能变化','可能的更新关系':sorted(best['relations'],key=lambda x:(x['较新序号'],x['较早序号'])),
          '说明':'不同事件的原话可能相关，也可能无关；当前状态尚未确认'}
    from .auto_vector_chunks import add_chunk_notice
    add_chunk_notice(db,scope,model_key,ids,data)
    if unresolved_hint_sources(db,scope,model_key,ids):data['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
    if boundary:data['采集情况']='先前存在已检测到的采集缺口，历史可能不完整'
    output=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    return True,output if len(output)<=budget else ''


def _merge_change_packets(db,scope,model_packet,rule_packet,budget,excluded):
    model=json.loads(model_packet);rule=json.loads(rule_packet)
    left=model['可能相关原话'];right=rule['可能相关原话']
    left_ids={x['序号'] for x in left};right_ids={x['序号'] for x in right}
    # 两组各自相关但没有共同来源，无法安全断言是同一变化链。
    if not left_ids & right_ids:return ''
    merged={}
    for row in left+right:
        seq=row['序号']
        if seq in merged and merged[seq]!=row:return ''
        merged[seq]=row
    ids=sorted(merged)
    if len(ids)>budget//10 or any('pending:'+str(seq) in excluded for seq in ids):return ''
    if any(separated(db,scope,a,b) for i,a in enumerate(ids) for b in ids[i+1:]):return ''
    rows=[merged[seq] for seq in ids]
    if any(datetime.fromisoformat(a['记录时间'])>=datetime.fromisoformat(b['记录时间']) for a,b in zip(rows,rows[1:])):return ''
    payload={'待审记录序号':ids[0],'待审相关序号':ids,'可能相关原话':rows,
        '线索方式':'跨事件组合变化线索','可能的更新关系':model.get('可能的更新关系',[]),
        '说明':'模型关系与文字变化线索均为推测；部分原话可能无关，当前状态尚未确认'}
    for field in ('变化核验提示','采集情况','检索提示'):
        if model.get(field) or rule.get(field):payload[field]=model.get(field) or rule.get(field)
    output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    return output if len(output)<=budget else ''


def cross_update_context(db,model_key,encoder,scope,query,cutoff,budget,message_id,excluded,*,_rule_only=False):
    """返回 (是否拦截其他自动供给, 内容)；来源关联始终只是推测。"""
    if not isinstance(message_id,str) or not 1<=len(message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query):return False,''
    try:
        version=db.execute('PRAGMA data_version').fetchone()[0]
        cutoff_time=datetime.fromisoformat(cutoff)
        if cutoff_time.utcoffset() is None:return True,''
        current_hash=hashlib.sha256((scope+'\0'+message_id).encode()).hexdigest()
        if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
            WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return True,''
        if not _rule_only:
            blocked,model_packet=_model_cross_context(db,model_key,encoder,scope,query,cutoff_time,budget,current_hash,excluded)
            if blocked:
                if not model_packet:return True,''
                # 模型线索可能早于更新词线索，不能直接返回而忽略后续原话。
                rule_blocked,rule_packet=cross_update_context(db,model_key,encoder,scope,query,cutoff,budget,message_id,excluded,_rule_only=True)
                if rule_blocked:
                    model_packet=_merge_change_packets(db,scope,model_packet,rule_packet,budget,excluded) if rule_packet else ''
                if db.execute('PRAGMA data_version').fetchone()[0]!=version:return True,''
                return True,model_packet
        ids=_indexed_candidates(db,scope,current_hash)
        if ids is None:
            # 历史索引尚未补全时，沿用原有全量路径，避免把部分候选当成全部。
            condition=' OR '.join('instr(c.content,?)>0' for _ in _CHANGE_WORDS)
            if not db.execute('SELECT 1 FROM dl_capture c WHERE c.scope=? AND c.state=\'pending\' AND c.event_hash<>? AND ('+condition+') LIMIT 1',
                              (scope,current_hash,*_CHANGE_WORDS)).fetchone():return False,''
        elif not ids:return False,''
        sql='''SELECT c.*,v.vector_json,m.topic_id,m.content_sha AS member_sha,m.recorded_at AS member_time FROM dl_capture c
            LEFT JOIN dl_auto_source_vectors v ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha
            LEFT JOIN dl_auto_topic_members m ON m.scope=c.scope AND m.seq=c.seq AND m.model_key=?
            WHERE c.scope=? AND c.state='pending' AND c.event_hash<>?'''
        params=(model_key,model_key,scope,current_hash)
        if ids is None:rows=db.execute(sql+' ORDER BY c.seq',params)
        else:
            # 分批查询，避免 SQLite 参数上限；按原始顺序恢复候选。
            found=[];ordered=sorted(ids)
            for start in range(0,len(ordered),500):
                chunk=ordered[start:start+500]
                found.extend(db.execute(sql+' AND c.seq IN ('+','.join('?' for _ in chunk)+')',(*params,*chunk)).fetchall())
            rows=sorted(found,key=lambda r:r['seq'])
        vectors=encoder.encode_queries([query])
        if not isinstance(vectors,(list,tuple)) or len(vectors)!=1:return True,''
        q=_unit(vectors[0]);gap=db.execute('SELECT total,last_at FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
        boundary=datetime.fromisoformat(gap['last_at']) if gap and gap['total'] else None
        if boundary and boundary.utcoffset() is None:return True,''
        entries=[];inverted={};matches=[];comparisons=0
        for r in rows:
            t=datetime.fromisoformat(r['recorded_at'])
            if t.utcoffset() is None or t>=cutoff_time or (boundary and t<=boundary):continue
            if (not r['vector_json'] or hashlib.sha256(r['content'].encode()).hexdigest()!=r['content_sha'] or
                (r['topic_id'] is not None and (r['member_sha']!=r['content_sha'] or r['member_time']!=r['recorded_at']))):return True,''
            vector=_unit(json.loads(r['vector_json']))
            if len(vector)!=len(q):return True,''
            score=sum(a*b for a,b in zip(q,vector));terms=_fourgrams(r['content'])
            if _CHANGE.search(r['content']):
                counts={}
                for term in terms:
                    candidates=inverted.get(term,());comparisons+=len(candidates)
                    if comparisons>100000:return True,''
                    for idx in candidates:counts[idx]=counts.get(idx,0)+1
                ranked=sorted(((n,idx) for idx,n in counts.items() if n>=1),reverse=True)
                relevant=[(n,idx) for n,idx in ranked if max(score,entries[idx][3])>=0.75]
                if relevant:
                    tied=[idx for n,idx in relevant if n==relevant[0][0]]
                    if len(tied)>1:
                        # 平分来源必须已由先前的推测线索连通，不能把多个相似事件强行串起。
                        linked={entries[tied[0]][0]['seq']};changed=True
                        while changed:
                            changed=False
                            for a,b in matches:
                                if a['seq'] in linked or b['seq'] in linked:
                                    before=len(linked);linked.update((a['seq'],b['seq']));changed|=len(linked)>before
                        if any(entries[idx][0]['seq'] not in linked for idx in tied):return True,''
                    old=entries[max(tied)]
                    matches.append((old[0],r))
            idx=len(entries);entries.append((r,t,terms,score))
            for term in terms:inverted.setdefault(term,[]).append(idx)
        if not matches:return False,''
        # 独立变化链分别竞争，避免另一件事的变化使所有候选被整体拒绝。
        parent={}
        def root(seq):
            parent.setdefault(seq,seq)
            while parent[seq]!=seq:
                parent[seq]=parent[parent[seq]];seq=parent[seq]
            return seq
        for a,b in matches:
            left,right=root(a['seq']),root(b['seq'])
            if left!=right:parent[right]=left
        scores={entry[0]['seq']:entry[3] for entry in entries}
        groups={}
        for a,b in matches:
            group=groups.setdefault(root(a['seq']),{'sources':{},'crossed':False,'score':-1.0})
            group['sources'].update({a['seq']:a,b['seq']:b})
            group['score']=max(group['score'],scores[a['seq']],scores[b['seq']])
            group['crossed']|=a['topic_id'] is None or b['topic_id'] is None or a['topic_id']!=b['topic_id']
        ranked=sorted(groups.values(),key=lambda group:group['score'],reverse=True)
        if len(ranked)>1 and ranked[0]['score']-ranked[1]['score']<0.10:return True,''
        best=ranked[0]
        if not best['crossed'] and not _rule_only:return False,''
        sources=best['sources'];ids=sorted(sources)
        # 保留完整变化链；如果数量连最小包装都不可能装进预算，直接拒绝。
        if len(ids)>budget//10:return True,''
        items=[sources[seq] for seq in ids]
        if (any('pending:'+str(r['seq']) in excluded or source_has_conflict(db,scope,model_key,r['seq']) for r in items) or
            any(separated(db,scope,a,b) for i,a in enumerate(ids) for b in ids[i+1:]) or
            any(datetime.fromisoformat(a['recorded_at'])>=datetime.fromisoformat(b['recorded_at']) for a,b in zip(items,items[1:]))):return True,''
        data={'待审记录序号':ids[0],'待审相关序号':ids,
              '可能相关原话':[{'序号':r['seq'],'原话':r['content'],'记录时间':r['recorded_at']} for r in items],
              '线索方式':'跨事件文字重合与变化词',
              '说明':'这些原话可能相关，也可能无关；变化对象和当前状态尚未确认'}
        from .auto_vector_chunks import add_chunk_notice
        add_chunk_notice(db,scope,model_key,ids,data)
        if unresolved_hint_sources(db,scope,model_key,ids):
            data['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
        if boundary:data['采集情况']='先前存在已检测到的采集缺口，历史可能不完整'
        output=json.dumps(data,ensure_ascii=False,separators=(',',':'))
        if db.execute('PRAGMA data_version').fetchone()[0]!=version or len(output)>budget:return True,''
        return True,output
    except Exception:return True,''
