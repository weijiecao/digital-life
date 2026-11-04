"""依据原话和向量建立可追溯的系统推测事件，不写入人工确认表。"""
import hashlib
import json
import re
import uuid
from datetime import datetime,timezone
from .engine.e419.dynamic_index import _unit
from .auto_organize import read_group,_SHORT_UPDATE
from .auto_model import separated


def _wake_rechecks_for_topic_change(db,scope,model_key,topic,changed_seq):
    """来源重新归组后，只重试与该事件有关且沿用相同授权策略的任务。"""
    rows=db.execute('''SELECT DISTINCT r.newer_seq FROM dl_auto_hint_rechecks r
        JOIN dl_auto_topic_members current ON current.scope=r.scope AND current.seq=r.newer_seq
            AND current.model_key=r.model_key AND current.topic_id=?
        JOIN dl_auto_model_jobs newer_job ON newer_job.scope=r.scope AND newer_job.seq=r.newer_seq
        JOIN dl_auto_topic_members candidate ON candidate.scope=r.scope AND candidate.model_key=r.model_key
            AND candidate.topic_id=current.topic_id AND candidate.seq<current.seq
        JOIN dl_auto_model_jobs candidate_job ON candidate_job.scope=candidate.scope AND candidate_job.seq=candidate.seq
            AND candidate_job.policy=newer_job.policy
        JOIN dl_capture c ON c.scope=candidate.scope AND c.seq=candidate.seq AND c.state='pending'
            AND c.content_sha=candidate.content_sha AND c.recorded_at=candidate.recorded_at
        JOIN dl_auto_source_vectors v ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=candidate.model_key
            AND v.content_sha=c.content_sha
        WHERE r.scope=? AND r.model_key=? AND (candidate.seq=? OR current.seq=?)
            AND newer_job.status IN ('done','skipped') AND newer_job.attempts<3''',
        (topic,scope,model_key,changed_seq,changed_seq)).fetchall()
    db.executemany('''UPDATE dl_auto_model_jobs SET status='pending' WHERE scope=? AND seq=?
        AND status IN ('done','skipped') AND attempts<3''',((scope,r['newer_seq']) for r in rows))
    return len(rows)


def _topic_rejects_source(db,scope,model_key,topic,seq):
    """只复核与来源直接相关的反向线索，但检查整个目标事件。"""
    rows=db.execute('''SELECT s.older_seq,s.newer_seq FROM dl_auto_separations s
        JOIN dl_auto_topic_members m ON m.scope=s.scope AND m.model_key=? AND m.topic_id=?
            AND m.seq=s.older_seq
        WHERE s.scope=? AND s.newer_seq=? AND s.verdict_version=1
        UNION ALL
        SELECT s.older_seq,s.newer_seq FROM dl_auto_separations s
        JOIN dl_auto_topic_members m ON m.scope=s.scope AND m.model_key=? AND m.topic_id=?
            AND m.seq=s.newer_seq
        WHERE s.scope=? AND s.older_seq=? AND s.verdict_version=1''',
        (model_key,topic,scope,seq,model_key,topic,scope,seq))
    return any(separated(db,scope,r['older_seq'],r['newer_seq']) for r in rows)


def sync_topics(db,model_key,limit=20):
    rows=db.execute('''SELECT c.*,v.vector_json FROM dl_capture c
        JOIN dl_auto_source_vectors v ON v.scope=c.scope AND v.seq=c.seq AND v.model_key=? AND v.content_sha=c.content_sha
        JOIN dl_auto_jobs j ON j.scope=c.scope AND j.seq=c.seq AND j.status='done'
        LEFT JOIN dl_auto_topic_members m ON m.scope=c.scope AND m.seq=c.seq AND m.model_key=?
        LEFT JOIN dl_auto_topic_skips k ON k.scope=c.scope AND k.seq=c.seq AND k.model_key=?
        WHERE c.state='pending' AND m.seq IS NULL AND k.seq IS NULL ORDER BY c.seq LIMIT ?''',
        (model_key,model_key,model_key,limit)).fetchall()
    for row in rows:
        try:
            version=db.execute('PRAGMA data_version').fetchone()[0]
            if hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']:continue
            stamp=datetime.fromisoformat(row['recorded_at'])
            if stamp.utcoffset() is None:raise ValueError('time')
            gap=db.execute('SELECT total,last_at FROM dl_capture_gaps WHERE scope=?',(row['scope'],)).fetchone()
            if gap and gap['total']:
                boundary=datetime.fromisoformat(gap['last_at'])
                if boundary.utcoffset() is None or stamp<=boundary:raise ValueError('gap')
            vec=_unit(json.loads(row['vector_json']));group=read_group(db,row['scope'],row['seq'])
            topic=None;basis='自动新话题'
            from .auto_topology import source_has_conflict
            if group:
                if any(separated(db,row['scope'],a,b) for i,a in enumerate(group['members']) for b in group['members'][i+1:]):
                    raise ValueError('conflicting group')
                peers=[x for x in group['members'] if x<row['seq']]
                memberships=set()
                for peer in peers:
                    if source_has_conflict(db,row['scope'],model_key,peer):raise ValueError('topic conflict')
                    prior=db.execute('SELECT topic_id FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?',
                                     (row['scope'],peer,model_key)).fetchone()
                    if prior:memberships.add(prior['topic_id'])
                if len(memberships)==1:topic=next(iter(memberships));basis='自动原话关联'
                elif len(memberships)>1:raise ValueError('ambiguous group')
                if topic and db.execute('''SELECT 1 FROM dl_auto_move_topics t JOIN dl_auto_topic_moves m ON m.id=t.move_id
                    WHERE t.scope=? AND t.topic_id=? AND m.model_key=? AND m.state='conflict' LIMIT 1''',
                    (row['scope'],topic,model_key)).fetchone():raise ValueError('topic conflict')
            if topic is None and _SHORT_UPDATE.match(row['content'].strip()):raise ValueError('unresolved update')
            if topic is not None:
                if _topic_rejects_source(db,row['scope'],model_key,topic,row['seq']):
                    raise ValueError('conflicting topic')
            if topic is None:
                candidates=db.execute('''SELECT m.topic_id,c.seq,c.content,c.content_sha,m.content_sha AS member_sha,
                    c.recorded_at,m.recorded_at AS member_time,v.vector_json FROM dl_auto_topic_members m
                    JOIN dl_capture c ON c.scope=m.scope AND c.seq=m.seq
                    JOIN dl_auto_source_vectors v ON v.scope=m.scope AND v.seq=m.seq AND v.model_key=m.model_key AND v.content_sha=c.content_sha
                    WHERE m.scope=? AND m.model_key=? AND m.seq<? AND c.state='pending' ORDER BY m.seq DESC''',
                    (row['scope'],model_key,row['seq']))
                scores={};denied=set();conflicts={}
                # 仅对确实存在反向线索的成员做来源复核，避免每个候选都查询一次。
                separate_rows=db.execute('''SELECT older_seq,newer_seq FROM dl_auto_separations
                    WHERE scope=? AND verdict_version=1 AND (older_seq=? OR newer_seq=?)''',
                    (row['scope'],row['seq'],row['seq'])).fetchall()
                opposing={x['older_seq'] if x['newer_seq']==row['seq'] else x['newer_seq'] for x in separate_rows}
                for item in candidates:
                    tid=item['topic_id']
                    if tid not in conflicts:conflicts[tid]=source_has_conflict(db,row['scope'],model_key,item['seq'])
                    if conflicts[tid]:denied.add(tid);continue
                    if item['seq'] in opposing and separated(db,row['scope'],item['seq'],row['seq']):
                        denied.add(item['topic_id']);continue
                    if item['content_sha']!=item['member_sha'] or item['recorded_at']!=item['member_time'] or hashlib.sha256(item['content'].encode()).hexdigest()!=item['content_sha']:raise ValueError('stale source')
                    t=datetime.fromisoformat(item['recorded_at'])
                    if t.utcoffset() is None or t>stamp:raise ValueError('time')
                    if gap and gap['total'] and t<=boundary:continue
                    v=_unit(json.loads(item['vector_json']))
                    if len(v)!=len(vec):raise ValueError('dimension')
                    score=sum(a*b for a,b in zip(v,vec));scores[item['topic_id']]=max(scores.get(item['topic_id'],-1),score)
                ranked=sorted(((k,v) for k,v in scores.items() if k not in denied),key=lambda x:(-x[1],x[0]))
                if ranked and ranked[0][1]>=0.85 and len(ranked)>1 and ranked[0][1]-ranked[1][1]<0.15:
                    raise ValueError('ambiguous topic')
                if ranked and ranked[0][1]>=0.85 and (len(ranked)==1 or ranked[0][1]-ranked[1][1]>=0.15):
                    topic=ranked[0][0];basis='本地语义相似'
            # 在取得写锁后重验候选快照，避免其他连接更新反向线索后仍提交旧归属。
            db.execute('BEGIN IMMEDIATE')
            with db:
                if db.execute('PRAGMA data_version').fetchone()[0]!=version:continue
                current=db.execute('SELECT content,content_sha,recorded_at,state FROM dl_capture WHERE scope=? AND seq=?',(row['scope'],row['seq'])).fetchone()
                if not current or current['state']!='pending' or current['content']!=row['content'] or current['content_sha']!=row['content_sha'] or current['recorded_at']!=row['recorded_at']:continue
                if topic is None:
                    topic='auto-'+uuid.uuid4().hex
                    db.execute('INSERT INTO dl_auto_topics VALUES(?,?,?,?)',(row['scope'],topic,model_key,datetime.now(timezone.utc).isoformat(timespec='seconds')))
                db.execute('INSERT INTO dl_auto_topic_members VALUES(?,?,?,?,?,?,?)',
                    (row['scope'],row['seq'],model_key,topic,row['content_sha'],row['recorded_at'],basis))
                # 模型任务可能在归组完成前被跳过；仅重试已有授权任务。
                db.execute('''UPDATE dl_auto_model_jobs SET status='pending' WHERE scope=? AND seq=?
                    AND status='skipped' AND attempts<3''',(row['scope'],row['seq']))
                _wake_rechecks_for_topic_change(db,row['scope'],model_key,topic,row['seq'])
                # 合并仍为推测，新加入成员依赖该合并；证据失效时需要重新定位。
                active=db.execute('''SELECT m.id FROM dl_auto_move_topics t JOIN dl_auto_topic_moves m ON m.id=t.move_id
                    WHERE t.scope=? AND t.topic_id=? AND m.model_key=? AND m.state='active' LIMIT 1''',
                    (row['scope'],topic,model_key)).fetchone()
                if active:
                    db.execute('INSERT OR REPLACE INTO dl_auto_move_dependents VALUES(?,?,?,?,?,?,?)',
                               (active['id'],row['scope'],row['seq'],model_key,topic,row['content_sha'],row['recorded_at']))
            # 给刚完成索引的来源补做针对性重定位，不依赖“最近 50 条”窗口。
            reconcile_topics(db,model_key,scope=row['scope'],seq=row['seq'])
        except (ValueError,TypeError,KeyError,OverflowError):
            with db:db.execute('INSERT OR REPLACE INTO dl_auto_topic_skips VALUES(?,?,?)',(row['scope'],row['seq'],model_key))
    return len(rows)


def reconcile_topics(db,model_key,limit=50,scope=None,seq=None):
    """新关联线索到达后合并无冲突的单条主题，不覆盖原话。"""
    if not isinstance(limit,int) or isinstance(limit,bool) or limit<1:return 0
    scan=scope is None or seq is None
    if scope is not None and seq is not None:
        rows=[{'scope':scope,'seq':seq}]
    else:
        cursor=db.execute('SELECT last_seq,last_scope FROM dl_auto_reconcile_cursor WHERE model_key=?',(model_key,)).fetchone()
        base='''SELECT scope,seq FROM (SELECT scope,seq FROM dl_auto_groups UNION SELECT scope,seq FROM dl_auto_chains
            UNION SELECT scope,seq FROM dl_auto_model_pairs)'''
        if cursor:
            rows=db.execute(base+' WHERE seq<? OR (seq=? AND scope<?) ORDER BY seq DESC,scope DESC LIMIT ?',
                            (cursor['last_seq'],cursor['last_seq'],cursor['last_scope'],limit)).fetchall()
        else:rows=[]
        if not rows:rows=db.execute(base+' ORDER BY seq DESC,scope DESC LIMIT ?',(limit,)).fetchall()
    seen=set();merged=0
    for row in rows:
        group=read_group(db,row['scope'],row['seq'])
        if not group:continue
        if any(separated(db,row['scope'],a,b) for i,a in enumerate(group['members']) for b in group['members'][i+1:]):continue
        key=(row['scope'],tuple(group['members']))
        if key in seen:continue
        seen.add(key)
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                # 写锁内重验线索，避免读取后其他连接改变归属或反向证据。
                current=read_group(db,row['scope'],row['seq'])
                if not current or current['members']!=group['members']:continue
                if any(separated(db,row['scope'],a,b) for i,a in enumerate(current['members']) for b in current['members'][i+1:]):continue
                members=[]
                for seq in group['members']:
                    member=db.execute('''SELECT m.*,c.content,c.content_sha AS actual_sha,c.recorded_at AS actual_time,c.state
                        FROM dl_auto_topic_members m JOIN dl_capture c ON c.scope=m.scope AND c.seq=m.seq
                        WHERE m.scope=? AND m.seq=? AND m.model_key=?''',(row['scope'],seq,model_key)).fetchone()
                    if (not member or member['state']!='pending' or member['content_sha']!=member['actual_sha'] or
                        member['recorded_at']!=member['actual_time'] or hashlib.sha256(member['content'].encode()).hexdigest()!=member['content_sha']):
                        members=[];break
                    members.append(member)
                if not members:continue
                counts={}
                for member in members:
                    tid=member['topic_id']
                    if tid not in counts:
                        counts[tid]=db.execute('SELECT COUNT(*) FROM dl_auto_topic_members WHERE scope=? AND topic_id=? AND model_key=?',
                                               (row['scope'],tid,model_key)).fetchone()[0]
                if len(counts)<2 or sum(n>1 for n in counts.values())>1:continue
                if any(db.execute('''SELECT 1 FROM dl_auto_move_topics t JOIN dl_auto_topic_moves m ON m.id=t.move_id
                    WHERE t.scope=? AND t.topic_id=? AND m.model_key=? AND m.state IN ('active','conflict') LIMIT 1''',
                    (row['scope'],tid,model_key)).fetchone() for tid in counts):continue
                root=next((k for k,v in counts.items() if v>1),members[0]['topic_id'])
                from .auto_topology import source_has_conflict
                if any(source_has_conflict(db,row['scope'],model_key,m['seq']) for m in members):continue
                if any(_topic_rejects_source(db,row['scope'],model_key,root,b['seq'])
                    for b in members if b['topic_id']!=root):continue
                for member in members:
                    if member['topic_id']==root:continue
                    db.execute("UPDATE dl_auto_topic_members SET topic_id=?,basis='系统线索重新定位' WHERE scope=? AND seq=? AND model_key=?",
                               (root,row['scope'],member['seq'],model_key))
                    _wake_rechecks_for_topic_change(db,row['scope'],model_key,root,member['seq'])
                    merged+=1
        except (ValueError,TypeError,KeyError):continue
    # 模型线索可能先于成员归组到达，或在冲突消除前暂时无法合并。
    # 复用同一游标重试多成员事件；实际合并再次在写锁中核验全部来源。
    from .auto_organize import read_model_group
    from .auto_topology import merge_model_topics
    seen_model=set()
    for row in rows:
        group=read_model_group(db,row['scope'],row['seq'])
        if not group or group['basis']!='模型提出的可能关联':continue
        pair=tuple(group['members']);identity=(row['scope'],pair)
        if identity in seen_model:continue
        seen_model.add(identity)
        topics=db.execute('''SELECT DISTINCT topic_id FROM dl_auto_topic_members
            WHERE scope=? AND model_key=? AND seq IN (?,?)''',
            (row['scope'],model_key,*pair)).fetchall()
        if len(topics)!=2:continue
        if merge_model_topics(db,model_key,row['scope'],row['seq']):merged+=1
    if scan and rows:
        with db:db.execute('INSERT OR REPLACE INTO dl_auto_reconcile_cursor VALUES(?,?,?)',
                           (model_key,rows[-1]['seq'],rows[-1]['scope']))
    return merged


def topic_context(db,model_key,encoder,scope,query,cutoff,budget,current_message_id,excluded,after_gap=None):
    if not isinstance(current_message_id,str) or not 1<=len(current_message_id)<=256 or re.search(r'不是|并非|别说|不指|不对|记错|认错|错了',query):return ''
    current_hash=hashlib.sha256((scope+'\0'+current_message_id).encode()).hexdigest()
    version=db.execute('PRAGMA data_version').fetchone()[0]
    try:
        limit=datetime.fromisoformat(cutoff);boundary=datetime.fromisoformat(after_gap) if after_gap else None
        if limit.utcoffset() is None or (boundary and boundary.utcoffset() is None):return ''
        if db.execute('''SELECT 1 FROM dl_auto_jobs j JOIN dl_capture c ON c.scope=j.scope AND c.seq=j.seq
            WHERE j.scope=? AND j.status='pending' AND c.event_hash<>? LIMIT 1''',(scope,current_hash)).fetchone():return ''
        if db.execute('''SELECT 1 FROM dl_capture c WHERE c.scope=? AND c.state='pending' AND c.event_hash<>?
            AND NOT EXISTS(SELECT 1 FROM dl_auto_source_vectors v WHERE v.scope=c.scope AND v.seq=c.seq
            AND v.model_key=? AND v.content_sha=c.content_sha) LIMIT 1''',(scope,current_hash,model_key)).fetchone():return ''
        rows=db.execute('''SELECT m.topic_id,m.content_sha AS member_sha,m.recorded_at AS member_time,
            c.*,v.vector_json FROM dl_auto_topic_members m JOIN dl_capture c ON c.scope=m.scope AND c.seq=m.seq
            JOIN dl_auto_source_vectors v ON v.scope=m.scope AND v.seq=m.seq AND v.model_key=m.model_key AND v.content_sha=c.content_sha
            WHERE m.scope=? AND m.model_key=? AND c.state='pending' ORDER BY c.seq''',(scope,model_key))
        vectors=encoder.encode_queries([query])
        if not isinstance(vectors,(list,tuple)) or len(vectors)!=1:return ''
        q=_unit(vectors[0]);groups={};scores={};blocked=set();conflicts={}
        negative=re.compile(r'不喜欢|不再|讨厌|没兴趣|不感兴趣|取消|改成|改为|更正')
        positive=re.compile(r'我(?:真的|很|特别|比较|一直|挺|还|最)?(?:喜欢|热爱|偏爱|爱吃|爱喝)')
        for row in rows:
            topic=row['topic_id'];stamp=datetime.fromisoformat(row['recorded_at'])
            from .auto_topology import source_has_conflict
            if topic not in conflicts:conflicts[topic]=source_has_conflict(db,scope,model_key,row['seq'])
            if conflicts[topic]:blocked.add(topic);continue
            if (row['content_sha']!=row['member_sha'] or row['recorded_at']!=row['member_time'] or
                hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha'] or stamp.utcoffset() is None or
                stamp>=limit or (boundary and stamp<=boundary) or row['event_hash']==current_hash or 'pending:'+str(row['seq']) in excluded):
                blocked.add(topic);continue
            v=_unit(json.loads(row['vector_json']))
            if len(v)!=len(q):return ''
            stats=groups.setdefault(topic,{'latest':stamp,'days':set(),'texts':set(),'negative':False,'count':0})
            stats['latest']=max(stats['latest'],stamp);stats['count']+=1
            if negative.search(row['content']):stats['negative']=True
            if positive.search(row['content']) and not any(c in row['content'] for c in '“”‘’「」『』"'):
                if len(stats['days'])<2:stats['days'].add(stamp.astimezone(timezone.utc).date())
                if len(stats['texts'])<2:stats['texts'].add(row['content'])
            score=sum(a*b for a,b in zip(q,v))
            scores[topic]=max(scores.get(topic,-1),score)
        ranked=[];boosts={}
        historical=bool(re.search(r'以前|曾经|过去|最初|当时|那年|那次',query))
        for topic,score in scores.items():
            if topic in blocked:continue
            stats=groups[topic];latest=stats['latest']
            age=max(0.0,(limit-latest).total_seconds()/86400.0)
            recency=0.0 if historical else 0.05*max(0.0,1-age/30.0)
            preference=0.05 if len(stats['days'])>=2 and len(stats['texts'])>=2 and not stats['negative'] else 0.0
            boosts[topic]=(recency,preference);ranked.append((score+recency+preference,score,topic))
        ranked.sort(reverse=True)
        if not ranked or ranked[0][1]<0.75 or (len(ranked)>1 and ranked[0][0]-ranked[1][0]<0.10):return ''
        selected=ranked[0][2]
        # 只对最终选中的事件保留原话，其他事件仅保留排序统计。
        items=db.execute('''SELECT m.topic_id,m.content_sha AS member_sha,m.recorded_at AS member_time,
            c.*,v.vector_json FROM dl_auto_topic_members m JOIN dl_capture c ON c.scope=m.scope AND c.seq=m.seq
            JOIN dl_auto_source_vectors v ON v.scope=m.scope AND v.seq=m.seq AND v.model_key=m.model_key AND v.content_sha=c.content_sha
            WHERE m.scope=? AND m.model_key=? AND m.topic_id=? AND c.state='pending' ORDER BY c.seq''',
            (scope,model_key,selected)).fetchall()
        source_scores={}
        for item in items:
            if (item['content_sha']!=item['member_sha'] or item['recorded_at']!=item['member_time'] or
                hashlib.sha256(item['content'].encode()).hexdigest()!=item['content_sha']):return ''
            v=_unit(json.loads(item['vector_json']))
            if len(v)!=len(q):return ''
            source_scores[item['seq']]=sum(a*b for a,b in zip(q,v))
        if len(items)<2:return ''
        # 即便只显示摘录，也必须确认所选事件的全部成员都参与过校验。
        count=db.execute('SELECT COUNT(*) FROM dl_auto_topic_members WHERE scope=? AND model_key=? AND topic_id=?',
                         (scope,model_key,selected)).fetchone()[0]
        if count!=len(items) or count!=groups[selected]['count']:return ''
        all_seqs={x['seq'] for x in items}
        # 不得遗漏紧跟原话的潜在更新；不确定时等待后续重新整理。
        # 先在完整作用域内计算相邻记录，再筛选事件成员；不能先过滤成员，
        # 否则会漏掉紧随其后的未归组更新。
        following_rows=db.execute('''WITH ordered AS (
            SELECT seq,recorded_at,LEAD(seq) OVER (ORDER BY seq) AS next_seq,
                LEAD(content) OVER (ORDER BY seq) AS next_content,
                LEAD(recorded_at) OVER (ORDER BY seq) AS next_time
            FROM dl_capture WHERE scope=?
        ) SELECT o.* FROM ordered o JOIN dl_auto_topic_members m
            ON m.scope=? AND m.model_key=? AND m.topic_id=? AND m.seq=o.seq''',
            (scope,scope,model_key,selected))
        for item in following_rows:
            if item['next_seq'] is not None and item['next_seq'] not in all_seqs and _SHORT_UPDATE.match(item['next_content'].strip()):
                t=datetime.fromisoformat(item['next_time']);old=datetime.fromisoformat(item['recorded_at'])
                if t.utcoffset() is None or 0<=(t-old).total_seconds()<=180:return ''
        total=len(items)
        from .auto_model import unresolved_hint_sources
        unresolved=unresolved_hint_sources(db,scope,model_key,all_seqs)
        if total>5:
            # 长事件仍需成对提供可能的变化及其上下文，不能只截取旧说法。
            change=re.compile(r'改成|改为|改到|调整为|推迟|提前|取消|不去了|不去|换成|更正|说错|纠正|不再')
            best=max(items,key=lambda x:(source_scores[x['seq']],x['seq']))
            keep={best['seq']}|{x['seq'] for x in items[-3:]}|(unresolved & all_seqs)
            for i,item in enumerate(items):
                if change.search(item['content']):
                    keep.add(item['seq'])
                    if i:keep.add(items[i-1]['seq'])
            # 模型判断可能关联的两端必须一同保留；已过期的判断不参与。
            from .auto_model import read_relation_hint
            ids={x['seq'] for x in items}
            hints=db.execute('''SELECT h.older_seq,h.newer_seq FROM dl_auto_relation_hints h
                JOIN dl_auto_topic_members a ON a.scope=h.scope AND a.seq=h.older_seq AND a.model_key=? AND a.topic_id=?
                JOIN dl_auto_topic_members b ON b.scope=h.scope AND b.seq=h.newer_seq AND b.model_key=a.model_key AND b.topic_id=a.topic_id
                WHERE h.scope=? LIMIT 201''',(model_key,selected,scope)).fetchall()
            if len(hints)>200:return ''
            for hint in hints:
                a,b=hint['older_seq'],hint['newer_seq']
                if a in ids and b in ids and read_relation_hint(db,scope,a,b):keep.update((a,b))
            # 关系过多时不截断变化链，等待更完整的检索处理。
            if len(keep)>10:return ''
            items=[x for x in items if x['seq'] in keep]
        seqs=[x['seq'] for x in items]
        payload={'待审记录序号':seqs[0],'待审相关序号':seqs,
            '可能相关原话':[{'序号':x['seq'],'原话':x['content'],'记录时间':x['recorded_at']} for x in items],
            '线索方式':'系统推测事件','定位方式':'本地语义相似',
            '说明':'系统自动归类的未确认线索；事件归属和新旧关系可能错误'}
        from .auto_vector_chunks import add_chunk_notice
        add_chunk_notice(db,scope,model_key,seqs,payload)
        from .auto_model import read_relation_hint
        hints=[];seen=set()
        for i,older in enumerate(seqs):
            for newer in seqs[i+1:]:
                hint=read_relation_hint(db,scope,older,newer)
                if hint:hints.append({'较早序号':older,'较新序号':newer,'关系':hint+'（模型推测，未确认）'})
        if hints:payload['可能的更新关系']=hints
        if unresolved:payload['变化核验提示']='曾有相关变化线索现已失效或正在重新定位；新旧关系及当前状态尚不确定'
        if any(boosts[selected]):payload['排序提示']='候选排序参考近期记录及重复自述线索，不能证明偏好或事实'
        if total>len(items):payload['覆盖说明']=f'仅展示 {len(items)} 条，共 {total} 条；未列出全部历史，不能据此断定当前状态'
        if any(db.execute('SELECT 1 FROM dl_import_provenance WHERE scope=? AND seq=?',(scope,x)).fetchone() for x in seqs):
            payload['时间来源']='部分时间来自导入文件，未经独立核验'
        if boundary:payload['采集情况']='先前存在已检测到的采集缺口，历史可能不完整；缺失内容无法恢复'
        output=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if db.execute('PRAGMA data_version').fetchone()[0]!=version:return ''
        return output if len(output)<=budget else ''
    except Exception:return ''
