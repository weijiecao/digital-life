"""Bounded, provisional automatic source localization; never confirms facts."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

_SHORT_UPDATE=re.compile(r'^(?:那件事|这件事|那个计划|这个计划|那次|原计划)?(?:改成|改为|改到|改去|改铺|改期|改签|挪到|挪至|换到|调到|降到|延期|延后|调整为|推迟|提前|取消|不去了|不去|不做了|不办了|去不了|换成|更正|说错|纠正|现在不|不再)')
# 同一任务连续失败到此次数后停止重试，避免长期每 5 分钟空转一次（P2-7）。
# 8 次按指数退避约覆盖 13 分钟，足以区分"暂时锁冲突"与"坏数据/代码缺陷"。
MAX_JOB_ATTEMPTS=8


def _grams(text):
    # 字面三字片段仅作候选线索，不代表语义、因果或事件归属。
    generic={'计划去','准备去','之前的','后来又','怎么样','是什么','这件事','那件事'}
    return {text[i:i+3] for i in range(len(text)-2)
            if all('\u4e00'<=c<='\u9fff' for c in text[i:i+3])
            and text[i:i+3] not in generic}


def organize_recent(db,scope,limit=60,through_seq=None):
    """Recompute provisional links within one scope; atomically replace this window."""
    rows=db.execute('''SELECT seq,content,content_sha,recorded_at FROM dl_capture
        WHERE scope=? AND state='pending' AND (? IS NULL OR seq<=?) ORDER BY seq DESC LIMIT ?''',
        (scope,through_seq,through_seq,limit)).fetchall()
    prepared=[]
    for row in rows:
        if hashlib.sha256(row['content'].encode('utf-8')).hexdigest()!=row['content_sha']:
            continue
        prepared.append((row,_grams(row['content'])))
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    results=[]
    for row,grams in prepared:
        candidates=[]
        for other,other_grams in prepared:
            if row['seq']==other['seq'] or row['content']==other['content']:continue
            shared=grams & other_grams
            if len(shared)>=2:
                candidates.append({'seq':other['seq'],'sha256':other['content_sha'],'overlap':len(shared)})
        candidates.sort(key=lambda x:(-x['overlap'],x['seq']))
        # 多个并列候选保持歧义；不能把匹配得分解释为事实置信度。
        state='unresolved' if not candidates else ('ambiguous' if len(candidates)>1 and candidates[0]['overlap']==candidates[1]['overlap'] else 'candidate')
        results.append((scope,row['seq'],row['content_sha'],state,
                        json.dumps(candidates[:5],ensure_ascii=False,separators=(',',':')),now))
    # 仅当双方都没有其他候选时，才把相邻的短句更新作为未确认线索。
    by_seq={r[1]:r for r in results}
    source_by_seq={r['seq']:r for r,_ in prepared}
    gap=db.execute('SELECT last_at,total FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
    for newer,_ in prepared:
        if not _SHORT_UPDATE.match(newer['content'].strip()):continue
        newer_result=by_seq[newer['seq']]
        if newer_result[3]!='unresolved':continue
        older=db.execute('''SELECT seq,content,content_sha,recorded_at,state FROM dl_capture
            WHERE scope=? AND seq<? ORDER BY seq DESC LIMIT 1''',(scope,newer['seq'])).fetchone()
        if not older or older['seq'] not in source_by_seq or older['state']!='pending':continue
        if _SHORT_UPDATE.match(older['content'].strip()) or '?' in older['content'] or '？' in older['content']:continue
        older_result=by_seq[older['seq']]
        if older_result[3]!='unresolved':continue
        try:
            old_time=datetime.fromisoformat(older['recorded_at']);new_time=datetime.fromisoformat(newer['recorded_at'])
            if old_time.utcoffset() is None or new_time.utcoffset() is None or not 0<=(new_time-old_time).total_seconds()<=180:continue
            if gap and gap['total']:
                boundary=datetime.fromisoformat(gap['last_at'])
                if boundary.utcoffset() is None or old_time<=boundary:continue
            following=db.execute('''SELECT content,recorded_at FROM dl_capture
                WHERE scope=? AND seq>? AND (? IS NULL OR seq<=?) ORDER BY seq LIMIT 1''',
                (scope,newer['seq'],through_seq,through_seq)).fetchone()
            if following and _SHORT_UPDATE.match(following['content'].strip()):
                t=datetime.fromisoformat(following['recorded_at'])
                if t.utcoffset() is None or 0<=(t-new_time).total_seconds()<=180:continue
        except (TypeError,ValueError,OverflowError):continue
        def possible(a,b):
            item={'seq':b['seq'],'sha256':b['content_sha'],'overlap':0,'method':'短句可能更新',
                  'source_time':a['recorded_at'],'peer_time':b['recorded_at']}
            return (scope,a['seq'],a['content_sha'],'candidate',json.dumps([item],ensure_ascii=False,separators=(',',':')),now)
        by_seq[older['seq']]=possible(older,newer)
        by_seq[newer['seq']]=possible(newer,older)
    results=list(by_seq.values())
    # 连续变化单独保存为完整链；只能作为未确认线索，绝不拆成两条事实。
    chains=[]
    for base,_ in prepared:
        if (_SHORT_UPDATE.match(base['content'].strip()) or '?' in base['content'] or
            '？' in base['content'] or by_seq[base['seq']][3]!='unresolved'):continue
        successors=db.execute('''SELECT seq,content,content_sha,recorded_at,state FROM dl_capture
            WHERE scope=? AND seq>? AND (? IS NULL OR seq<=?) ORDER BY seq LIMIT 5''',
            (scope,base['seq'],through_seq,through_seq)).fetchall()
        chain=[base];valid=True
        for item in successors:
            if not _SHORT_UPDATE.match(item['content'].strip()):break
            if len(chain)>=5:valid=False;break
            if (item['state']!='pending' or item['seq'] not in by_seq or
                by_seq[item['seq']][3]!='unresolved' or item['seq'] not in source_by_seq):
                valid=False;break
            try:
                t0=datetime.fromisoformat(chain[-1]['recorded_at']);t1=datetime.fromisoformat(item['recorded_at'])
                if t0.utcoffset() is None or t1.utcoffset() is None or not 0<=(t1-t0).total_seconds()<=180:
                    valid=False;break
            except (ValueError,TypeError,OverflowError):valid=False;break
            chain.append(item)
        if not valid or len(chain)<3:continue
        try:
            if gap and gap['total']:
                boundary=datetime.fromisoformat(gap['last_at']);start=datetime.fromisoformat(base['recorded_at'])
                if boundary.utcoffset() is None or start<=boundary:continue
        except (ValueError,TypeError,OverflowError):continue
        manifest=[{'seq':item['seq'],'sha256':item['content_sha'],'recorded_at':item['recorded_at']} for item in chain]
        encoded=json.dumps(manifest,ensure_ascii=False,separators=(',',':'))
        chains.extend((scope,item['seq'],base['seq'],encoded,now) for item in chain)
    with db:
        # 任务重跑常会重写相同分组；只在实际依据改变时唤醒搁置来源。
        affected=[r[1] for r in results]
        def group_state():
            if not affected:return {}
            marks=','.join('?' for _ in affected)
            groups={r['seq']:(r['peer_seq'],r['source_sha'],r['peer_sha']) for r in
                db.execute('SELECT seq,peer_seq,source_sha,peer_sha FROM dl_auto_groups WHERE scope=? AND seq IN ('+marks+')',(scope,*affected))}
            chains={r['seq']:(r['anchor_seq'],r['manifest_json']) for r in
                db.execute('SELECT seq,anchor_seq,manifest_json FROM dl_auto_chains WHERE scope=? AND seq IN ('+marks+')',(scope,*affected))}
            return {seq:(groups.get(seq),chains.get(seq)) for seq in affected}
        before=group_state()
        db.executemany('''INSERT OR REPLACE INTO dl_auto_localization
            (scope,seq,source_sha,state,candidates_json,updated_at) VALUES(?,?,?,?,?,?)''',results)
        db.executemany('DELETE FROM dl_auto_chains WHERE scope=? AND seq=?',((scope,r[1]) for r in results))
        db.executemany('INSERT OR REPLACE INTO dl_auto_chains VALUES(?,?,?,?,?)',chains)
        # 仅互相唯一匹配的两条记录建立临时分组，仍是系统猜测。
        by_seq={r[1]:r for r in results}
        for result in results:
            _,seq,sha,state,candidate_json,_=result
            db.execute('DELETE FROM dl_auto_groups WHERE scope=? AND seq=?',(scope,seq))
            if state!='candidate':continue
            candidate=json.loads(candidate_json)[0];other=by_seq.get(candidate['seq'])
            if not other or other[3]!='candidate':continue
            reverse=json.loads(other[4])
            if not reverse or reverse[0]['seq']!=seq:continue
            db.execute('INSERT INTO dl_auto_groups VALUES(?,?,?,?,?,?)',
                (scope,seq,candidate['seq'],sha,candidate['sha256'],now))
        after=group_state();retry=set()
        for seq in affected:
            if before.get(seq)==after.get(seq):continue
            retry.add(seq)
            for state in (before.get(seq),after.get(seq)):
                if state and state[0]:retry.add(state[0][0])
        db.executemany('DELETE FROM dl_auto_topic_skips WHERE scope=? AND seq=?',((scope,seq) for seq in retry))
    return len(results)


def process_jobs(db,batch_size=10):
    """补排旧数据并处理有限任务；失败退避，进程重启后继续。"""
    with db:
        db.execute('''INSERT OR IGNORE INTO dl_auto_jobs(scope,seq)
            SELECT c.scope,c.seq FROM dl_capture c LEFT JOIN dl_auto_jobs j
            ON j.scope=c.scope AND j.seq=c.seq
            WHERE c.state='pending' AND j.seq IS NULL ORDER BY c.seq LIMIT 200''')
    now=datetime.now(timezone.utc)
    processed=0
    for _ in range(batch_size):
        # 同一会话严格按序处理；较早任务等待重试时，其他会话仍可继续。
        job=db.execute('''SELECT j.scope,j.seq,j.attempts FROM dl_auto_jobs j
            WHERE j.status='pending' AND j.next_at<=? AND NOT EXISTS
              (SELECT 1 FROM dl_auto_jobs prior WHERE prior.scope=j.scope
               AND prior.status='pending' AND prior.seq<j.seq)
            ORDER BY j.seq LIMIT 1''',(now.isoformat(timespec='seconds'),)).fetchone()
        if not job:break
        processed+=1
        try:
            organize_recent(db,job['scope'],through_seq=job['seq'])
            with db:db.execute("UPDATE dl_auto_jobs SET status='done' WHERE scope=? AND seq=?",
                               (job['scope'],job['seq']))
        except Exception:
            db.rollback()
            attempts=job['attempts']+1
            if attempts>=MAX_JOB_ATTEMPTS:
                # 死信：停止重试但保留原话，缺口可见（/dlm 记忆状态 会报数）。
                with db:db.execute('''UPDATE dl_auto_jobs SET status='dead',attempts=?,next_at=''
                    WHERE scope=? AND seq=?''',(attempts,job['scope'],job['seq']))
            else:
                delay=min(300,2**min(attempts,8))
                next_at=(now+timedelta(seconds=delay)).isoformat(timespec='seconds')
                with db:db.execute('''UPDATE dl_auto_jobs SET attempts=attempts+1,next_at=?
                    WHERE scope=? AND seq=?''',(next_at,job['scope'],job['seq']))
    return processed


def read_group(db,scope,seq):
    """读取来源仍有效、双方仍相互指向的临时分组。"""
    group=_read_group_unchecked(db,scope,seq)
    if group:
        from .auto_model import separated
        members=group['members']
        if any(separated(db,scope,a,b) for i,a in enumerate(members) for b in members[i+1:]):return None
    return group


def _read_group_unchecked(db,scope,seq):
    chain=read_chain(db,scope,seq)
    if chain:return chain
    row=db.execute('SELECT * FROM dl_auto_groups WHERE scope=? AND seq=?',(scope,seq)).fetchone()
    if not row:return read_model_group(db,scope,seq)
    first=read_localization(db,scope,seq);second=read_localization(db,scope,row['peer_seq'])
    if not first or not second or first['state']!='candidate' or second['state']!='candidate':return None
    if first['candidates'][0]['seq']!=row['peer_seq'] or second['candidates'][0]['seq']!=seq:return None
    if first['candidates'][0]['sha256']!=row['peer_sha'] or second['candidates'][0]['sha256']!=row['source_sha']:return None
    if first['candidates'][0].get('method')=='短句可能更新':
        gap=db.execute('SELECT total,last_at FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
        if gap and gap['total']:
            try:
                boundary=datetime.fromisoformat(gap['last_at'])
                t1=datetime.fromisoformat(first['candidates'][0]['source_time'])
                t2=datetime.fromisoformat(first['candidates'][0]['peer_time'])
                if boundary.utcoffset() is None or min(t1,t2)<=boundary:return None
            except (ValueError,TypeError,OverflowError):return None
    return {'members':sorted([seq,row['peer_seq']]),'status':'系统临时分组，未确认',
            'basis':first['candidates'][0].get('method','字面重合')}


def read_model_group(db,scope,seq):
    from .auto_model import _valid_pair
    row=db.execute('SELECT * FROM dl_auto_model_pairs WHERE scope=? AND seq=?',(scope,seq)).fetchone()
    if not row:return None
    try:
        manifest=json.loads(row['manifest_json'])
        if not isinstance(manifest,list) or len(manifest)!=2:return None
        ids=[x['seq'] for x in manifest]
        if ids!=sorted(set(ids)) or seq not in ids or row['peer_seq'] not in ids or row['peer_seq']==seq:return None
        sources=[]
        for item in manifest:
            source=db.execute('SELECT content FROM dl_capture WHERE scope=? AND seq=?',(scope,item['seq'])).fetchone()
            if not source:return None
            sources.append({**item,'content':source['content']})
        if not _valid_pair(db,scope,sources):return None
        reverse=db.execute('SELECT * FROM dl_auto_model_pairs WHERE scope=? AND seq=?',(scope,row['peer_seq'])).fetchone()
        if not reverse or reverse['peer_seq']!=seq or reverse['manifest_json']!=row['manifest_json']:return None
        later=db.execute('SELECT content,recorded_at FROM dl_capture WHERE scope=? AND seq>? ORDER BY seq LIMIT 1',(scope,ids[-1])).fetchone()
        if later and _SHORT_UPDATE.match(later['content'].strip()):
            after=datetime.fromisoformat(later['recorded_at']);last=datetime.fromisoformat(sources[-1]['recorded_at'])
            if after.utcoffset() is None or 0<=(after-last).total_seconds()<=180:return None
        result={'members':ids,'status':'模型可能关联，未确认','basis':'模型提出的可能关联'}
        hint=db.execute('''SELECT kind,manifest_json FROM dl_auto_relation_hints
            WHERE scope=? AND newer_seq=? AND older_seq=?''',(scope,ids[1],ids[0])).fetchone()
        if hint and hint['manifest_json']==row['manifest_json'] and hint['kind'] in ('update','correction'):
            result['relation_hint']='可能更新' if hint['kind']=='update' else '可能纠正'
        return result
    except (ValueError,TypeError,KeyError,OverflowError):return None


def read_chain(db,scope,seq):
    """校验完整链、时间及来源；不能证明这些原话确属同一事件。"""
    row=db.execute('SELECT * FROM dl_auto_chains WHERE scope=? AND seq=?',(scope,seq)).fetchone()
    if not row:return None
    try:
        manifest=json.loads(row['manifest_json'])
        if not isinstance(manifest,list) or not 3<=len(manifest)<=5:return None
        ids=[x['seq'] for x in manifest]
        if ids!=sorted(set(ids)) or seq not in ids or ids[0]!=row['anchor_seq']:return None
        current=db.execute('''SELECT seq,content,content_sha,recorded_at,state FROM dl_capture
            WHERE scope=? AND seq>=? AND seq<=? ORDER BY seq''',(scope,ids[0],ids[-1])).fetchall()
        if [x['seq'] for x in current]!=ids:return None
        previous=None
        for i,(item,stored) in enumerate(zip(current,manifest)):
            if (item['state']!='pending' or item['content_sha']!=stored['sha256'] or
                item['recorded_at']!=stored['recorded_at'] or
                hashlib.sha256(item['content'].encode()).hexdigest()!=stored['sha256']):return None
            timestamp=datetime.fromisoformat(item['recorded_at'])
            if timestamp.utcoffset() is None:return None
            if i and (not _SHORT_UPDATE.match(item['content'].strip()) or
                      not 0<=(timestamp-previous).total_seconds()<=180):return None
            previous=timestamp
        gap=db.execute('SELECT total,last_at FROM dl_capture_gaps WHERE scope=?',(scope,)).fetchone()
        if gap and gap['total']:
            boundary=datetime.fromisoformat(gap['last_at'])
            if boundary.utcoffset() is None or datetime.fromisoformat(current[0]['recorded_at'])<=boundary:return None
        following=db.execute('''SELECT content,recorded_at FROM dl_capture WHERE scope=? AND seq>?
            ORDER BY seq LIMIT 1''',(scope,ids[-1])).fetchone()
        if following and _SHORT_UPDATE.match(following['content'].strip()):
            t=datetime.fromisoformat(following['recorded_at'])
            if t.utcoffset() is None or 0<=(t-previous).total_seconds()<=180:return None
        return {'members':ids,'status':'系统临时分组，未确认','basis':'连续短句可能更新'}
    except (TypeError,KeyError,ValueError,OverflowError):return None


def read_localization(db,scope,seq):
    """Only return current source-bound candidate records; mismatches fail closed."""
    row=db.execute('''SELECT a.*,c.content,c.content_sha,c.recorded_at FROM dl_auto_localization a
        JOIN dl_capture c ON c.scope=a.scope AND c.seq=a.seq
        WHERE a.scope=? AND a.seq=? AND c.state='pending' ''',(scope,seq)).fetchone()
    if not row or row['source_sha']!=row['content_sha'] or hashlib.sha256(row['content'].encode()).hexdigest()!=row['source_sha']:
        return None
    candidates=json.loads(row['candidates_json'])
    for item in candidates:
        source=db.execute('SELECT content,content_sha,recorded_at FROM dl_capture WHERE scope=? AND seq=? AND state=\'pending\'',(scope,item['seq'])).fetchone()
        if not source or source['content_sha']!=item['sha256'] or hashlib.sha256(source['content'].encode()).hexdigest()!=item['sha256']:
            return None
        if item.get('method')=='短句可能更新' and (item.get('source_time')!=row['recorded_at'] or item.get('peer_time')!=source['recorded_at']):
            return None
    return {'state':row['state'],'candidates':candidates,'basis':'字面三字片段，未经确认'}
