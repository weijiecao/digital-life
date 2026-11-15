"""E4.18: unified source-preserving ingest/review/retrieve.

安全前提：scope、origin、session、reviewer 均由可信上游鉴别；本类不包含身份认证。
事件标题和阶段只能是字面锚点/审核结果，不构成自主语义事实核验。
"""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Callable
from ..lifemem.core import MemoryCore, MissingRecord, required
from ..e417.writer import MemoryWriter, PHASES, _digest

_STRONG_PHASES={
 'earliest': ('最初','最早','原本','起初','一开始','原计划'),
 'change': ('后来','改成','改为','修改','调整','变更','中途'),
 'paused': ('暂停','搁置','中止'),
 'latest': ('最终','最后','结果','现在','目前','当前','完成','取消'),
}
_CONFIRM=re.compile(r'^(?:对[，,。！! ]*|是的[，,。！! ]*|没错[，,。！! ]*|就是这(?:件|个)[事]?[，,。！! ]*|对，就是这(?:件|个)[事]?[，,。！! ]*)$')
_REJECT=re.compile(r'(?:不是这[个件]事|不是它|不是你说的|认错了|记错了|不是那个)')
_CLEAR=re.compile(r'(?:换个话题|不聊这件事|结束这个话题)')
_CORRECTION=re.compile(r'(?:不对|我说错了|其实是|纠正一下|改成|改为|不是.*而是)')
_FOLLOWUP=re.compile(r'(?:那件事|那个事|这件事|这个事|它)|^(?:那)?(?:后来|最后|最终|结果|最初|最早)(?:呢|怎么样|怎么样了|如何|怎么了|是什么|怎样|发生了什么)[？?。！!]*$')
_GENERIC={'的','了','是','吗','啊','呢','事','那个','这个','一下','之前','后来','怎么','怎么样'}

class MemoryFlowError(ValueError):
    pass

def _fingerprint(rows:list[dict], rels:list[dict])->str:
    payload={'sources':[{'id':r['id'],'sha':_digest(r['content']), 'phase':r['phase'],
                         'recorded_at':r['recorded_at'],'occurred_at':r['occurred_at']}
                        for r in rows], 'relations':rels}
    return sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()

class UnifiedMemoryEngine:
    """单一接口管理入库、审阅、话题连续性、来源选择，绝不自动认可模型猜测。"""
    def __init__(self,path, candidate_fn:Callable[[str,str],list[str]]|None=None):
        self.core=MemoryCore(path)
        self.writer=MemoryWriter(self.core)
        self.db=self.core.db
        self.candidate_fn=candidate_fn
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS e418_focus (
          scope TEXT NOT NULL,session TEXT NOT NULL,mode TEXT NOT NULL,
          event_id TEXT NOT NULL DEFAULT '',digest TEXT NOT NULL DEFAULT '',
          offered INTEGER NOT NULL DEFAULT 0,turn_serial INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(scope,session));
        CREATE TABLE IF NOT EXISTS e418_turns (
          serial INTEGER PRIMARY KEY AUTOINCREMENT,
          scope TEXT NOT NULL,session TEXT NOT NULL,origin TEXT,
          query_sha256 TEXT NOT NULL,action TEXT NOT NULL,status TEXT NOT NULL,
          event_id TEXT NOT NULL DEFAULT '',digest TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_e418_turn_scope ON e418_turns(scope,session,serial);
        ''')
        self.db.commit()

    def close(self):self.core.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

    def ingest(self,*,scope,origin,speaker,kind,content,recorded_at,occurred_at=None):
        """任何用户新话进入 pending；助手已发送内容永不提升为用户证据。"""
        return self.writer.ingest(scope=scope,origin=origin,speaker=speaker,kind=kind,
                    content=content,recorded_at=recorded_at,occurred_at=occurred_at)

    def review(self,*,scope,origin,decision,reviewer,event_id=None,phase=None,reason=''):
        """审阅者身份与决定由可信上游提供，本模块不能独立证明其正确。"""
        return self.writer.review(scope=scope,origin=origin,decision=decision,
                   reviewer=reviewer,event_id=event_id,phase=phase,reason=reason)

    def relate(self,**kwargs):return self.writer.relate(**kwargs)
    def pending(self,scope):return self.writer.pending(scope)
    def audit(self,scope):return self.writer.audit(scope)

    def _events(self,scope):
        # 只把具有“已审核用户原话”的事件暴露给检索，不暴露待审事件壳。
        rows=self.db.execute('''SELECT DISTINCT e.id,e.title FROM events e
            JOIN e417_phase p ON e.scope=p.scope AND e.id=p.event_id
            JOIN e417_ingest i ON i.scope=p.scope AND i.message_id=p.message_id
            JOIN messages m ON m.scope=i.scope AND m.id=i.message_id
            WHERE e.scope=? AND i.state='approved_link' AND m.speaker='user'
            ORDER BY length(e.title) DESC,e.id''',(scope,)).fetchall()
        return [dict(r) for r in rows]

    def _event_rows(self,scope,event_id):
        rows=self.db.execute('''SELECT m.id,m.content,m.recorded_at,m.occurred_at,
                         p.phase,i.source_sha256 FROM e417_phase p
            JOIN e417_ingest i ON p.scope=i.scope AND p.message_id=i.message_id
            JOIN messages m ON m.scope=i.scope AND m.id=i.message_id
            WHERE p.scope=? AND p.event_id=? AND i.state='approved_link' AND m.speaker='user'
            ORDER BY m.recorded_at,m.id''',(scope,event_id)).fetchall()
        result=[dict(r) for r in rows]
        rel=[dict(r) for r in self.db.execute('''SELECT newer,older,kind FROM relations
            WHERE scope=? AND newer IN (SELECT message_id FROM e417_phase WHERE scope=? AND event_id=?)
              AND older IN (SELECT message_id FROM e417_phase WHERE scope=? AND event_id=?)
            ORDER BY newer,older,kind''',(scope,scope,event_id,scope,event_id))]
        return result,rel

    def _snapshot(self,scope,event_id):
        event=self.db.execute('SELECT id,title FROM events WHERE scope=? AND id=?',(scope,event_id)).fetchone()
        if not event:return None
        rows,relations=self._event_rows(scope,event_id)
        if not rows:return None
        stale=any(_digest(r['content'])!=r['source_sha256'] for r in rows)
        return {'id':event_id,'title':event['title'],'rows':rows,'stale':stale,
                'digest':_fingerprint(rows,relations)}

    def _phase(self,question):
        hits=[key for key,words in _STRONG_PHASES.items() if any(w in question for w in words)]
        return (hits[0],False) if len(hits)==1 else ('all' if not hits else 'all',len(hits)>1)

    def _focus(self,scope,session):
        row=self.db.execute('SELECT * FROM e418_focus WHERE scope=? AND session=?',(scope,session)).fetchone()
        return dict(row) if row else None

    def _store(self,scope,session,mode,event_id='',digest='',offered=0):
        with self.db:
            self.db.execute('''INSERT INTO e418_focus(scope,session,mode,event_id,digest,offered,turn_serial)
                VALUES(?,?,?,?,?,?,COALESCE((SELECT max(serial) FROM e418_turns
                     WHERE scope=? AND session=?),0))
                ON CONFLICT(scope,session) DO UPDATE SET mode=excluded.mode,
                  event_id=excluded.event_id,digest=excluded.digest,offered=excluded.offered,
                  turn_serial=excluded.turn_serial''',
                (scope,session,mode,event_id,digest,int(offered),scope,session))

    def _log(self,scope,session,question,action,status,event_id='',digest='',origin=None):
        with self.db:self.db.execute('''INSERT INTO e418_turns
            (scope,session,origin,query_sha256,action,status,event_id,digest,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',(scope,session,origin,
                 sha256(question.encode('utf-8')).hexdigest(),action,status,event_id,digest,
                 datetime.now(timezone.utc).isoformat(timespec='seconds')))

    @staticmethod
    def _packet(status,reason,phase,budget,*,event=None,quotes=None,associations=None,skipped=None):
        quotes=quotes or [];associations=associations or [];skipped=skipped or []
        content=''.join(q['line'] for q in quotes)
        assert len(content)<=budget
        return {'status':status,'reason':reason,'phase':phase,
                'event_id':event['id'] if event else None,
                'event_title':event['title'] if event else None,
                'source_fingerprint':event['digest'] if event else None,
                'fact_evidence':[{k:q[k] for k in ('message_id','phase','recorded_at','occurred_at','verbatim')}
                                 for q in quotes],
                'associations':associations,'context':content,'context_chars':len(content),
                'budget_chars':budget,'skipped_source_ids':skipped,
                'may_quote_user':bool(quotes) and status=='USER_QUOTE_AVAILABLE',
                'may_assert_world_fact':False}

    def _quote(self,scope,event_id,phase,budget):
        e=self._snapshot(scope,event_id)
        if e is None:return self._packet('NO_REVIEWED_EVIDENCE','no_approved_event',phase,budget)
        if e['stale']:return self._packet('STALE_SOURCE','approved_raw_has_changed',phase,budget,event=e)
        # 来源过期前不应断言现阶段状态：有任何同事件待审修改就先提醒。
        pending=self.db.execute('''SELECT count(*) FROM e417_ingest
          WHERE scope=? AND candidate_event_id=? AND state='pending_review' ''',
          (scope,event_id)).fetchone()[0]
        if pending:
            # 不能把旧阶段当成当前结论，但不必因此丢掉已经审阅的历史。
            # 单独的 historical_context 只表示当时用户说过什么，不是现况。
            packet=self._packet('PENDING_UPDATE','unreviewed_new_message_may_change_event',phase,budget,event=e)
            historical=[];skipped=[];used=0
            for r in e['rows']:
                if phase!='all' and r['phase']!=phase:
                    continue
                line=f"[HISTORICAL_ONLY | {r['id']} | {r['recorded_at']} | {r['phase']} | user said] {r['content']}\n"
                if used+len(line)>budget:
                    skipped.append(r['id']);continue
                historical.append({'message_id':r['id'],'phase':r['phase'],
                                   'recorded_at':r['recorded_at'],
                                   'occurred_at':r['occurred_at'],'verbatim':r['content'],
                                   'line':line})
                used+=len(line)
            packet['historical_evidence']=[{k:v for k,v in r.items() if k!='line'} for r in historical]
            packet['historical_context']=''.join(r['line'] for r in historical)
            packet['historical_context_chars']=used
            packet['historical_skipped_source_ids']=skipped
            packet['may_quote_historical']=bool(historical)
            packet['may_assert_current_state']=False
            packet['pending_update_count']=pending
            return packet
        selected=[r for r in e['rows'] if phase=='all' or r['phase']==phase]
        if not selected:
            return self._packet('NO_PHASE_EVIDENCE','requested_phase_has_no_reviewed_source',phase,budget,event=e)
        quotes=[];skipped=[];size=0
        for r in selected:
            line=f"[{r['id']} | {r['recorded_at']} | {r['phase']} | user said] {r['content']}\n"
            if size+len(line)>budget:
                skipped.append(r['id']);continue
            quotes.append({'message_id':r['id'],'phase':r['phase'],'recorded_at':r['recorded_at'],
                           'occurred_at':r['occurred_at'],'verbatim':r['content'],'line':line})
            size+=len(line)
        if not quotes:return self._packet('BUDGET_INSUFFICIENT','full_raw_source_does_not_fit',phase,budget,event=e,skipped=skipped)
        status='USER_QUOTE_AVAILABLE' if not skipped else 'PARTIAL_SOURCE_COVERAGE'
        return self._packet(status,'approved_user_verbatim_not_objective_world_truth',phase,budget,
                            event=e,quotes=quotes,skipped=skipped)

    def _candidate_ids(self,scope,question,events):
        if self.candidate_fn:
            ids=self.candidate_fn(scope,question)
            if not isinstance(ids,(list,tuple)) or len(ids)>20:raise MemoryFlowError('untrusted candidates shape')
            known={e['id'] for e in events}
            return list(dict.fromkeys(x for x in ids if isinstance(x,str) and x in known))[:5]
        ranked=[]
        for event in events:
            title=event['title']
            fragments={title[i:i+2] for i in range(len(title)-1)}
            score=sum(1 for frag in fragments if frag in question and frag not in _GENERIC)
            if score:ranked.append((-score,event['id']))
        return [eid for _,eid in sorted(ranked)[:5]]

    def query(self,*,scope,session,question,budget_chars=900,origin=None):
        scope=required(scope,'scope');session=required(session,'session');question=required(question,'question')
        if type(budget_chars) is not int or budget_chars<1:raise MemoryFlowError('budget must be positive integer')
        phase,ambiguous_phase=self._phase(question)
        prior=self._focus(scope,session)
        events=self._events(scope)
        negated_ids={e['id'] for e in events if re.search(
            r'(?:不是|并非|不指|别说)\s*'+re.escape(e['title']),question)}
        matches=[e for e in events if e['title'] in question and e['id'] not in negated_ids]
        event=None;action='unresolved';packet=None
        if _CLEAR.search(question):
            action='clear';self._store(scope,session,'cleared')
            packet=self._packet('NO_EVIDENCE','explicit_topic_clear',phase,budget_chars)
        elif _REJECT.search(question):
            action='reject';self._store(scope,session,'rejected')
            packet=self._packet('REJECTED_MATCH','user_denied_prior_referent',phase,budget_chars)
        elif len(matches)>1:
            action='multiple_titles';self._store(scope,session,'unresolved')
            candidates=[{'event_id':e['id'],'title':e['title'],'status':'ASSOCIATION_ONLY'} for e in matches[:5]]
            packet=self._packet('AMBIGUOUS_REFERENCE','multiple_approved_event_titles',phase,budget_chars,associations=candidates)
        elif len(matches)==1:
            action='explicit_title';event=self._snapshot(scope,matches[0]['id'])
            if event:
                self._store(scope,session,'explicit',event['id'],event['digest'])
                packet=self._packet('AMBIGUOUS_PHASE','multiple_time_hints',phase,budget_chars,event=event) if ambiguous_phase else self._quote(scope,event['id'],phase,budget_chars)
        elif _CONFIRM.fullmatch(question) and prior and prior['mode']=='candidate' and prior['offered']:
            event=self._snapshot(scope,prior['event_id'])
            action='confirm_candidate'
            if not event or event['digest']!=prior['digest']:
                self._store(scope,session,'unresolved')
                packet=self._packet('STALE_REFERENCE','candidate_snapshot_changed',phase,budget_chars)
            else:
                self._store(scope,session,'confirmed',event['id'],event['digest'])
                packet=self._quote(scope,event['id'],phase,budget_chars)
        elif prior and prior['mode'] in ('explicit','confirmed') and _FOLLOWUP.search(question) and not _CORRECTION.search(question):
            event=self._snapshot(scope,prior['event_id'])
            action='followup'
            if not event or event['digest']!=prior['digest']:
                self._store(scope,session,'unresolved')
                packet=self._packet('STALE_REFERENCE','confirmed_context_has_changed',phase,budget_chars)
            else:
                packet=self._packet('AMBIGUOUS_PHASE','multiple_time_hints',phase,budget_chars,event=event) if ambiguous_phase else self._quote(scope,event['id'],phase,budget_chars)
        else:
            ids=[eid for eid in self._candidate_ids(scope,question,events) if eid not in negated_ids]
            action='candidate'
            if ids:
                associations=[]
                for eid in ids:
                    e=self._snapshot(scope,eid)
                    if e and not e['stale']:
                        associations.append({'event_id':eid,'title':e['title'],'source_fingerprint':e['digest'],
                                             'status':'ASSOCIATION_ONLY'})
                if len(associations)==1:
                    a=associations[0]
                    self._store(scope,session,'candidate',a['event_id'],a['source_fingerprint'])
                else:self._store(scope,session,'unresolved')
                packet=self._packet('ASSOCIATIONS_ONLY' if associations else 'NO_EVIDENCE',
                            'similarity_is_not_identity_evidence',phase,budget_chars,associations=associations)
            else:
                self._store(scope,session,'unresolved')
                packet=self._packet('NO_EVIDENCE','no_approved_candidate',phase,budget_chars)
        if packet is None:
            packet=self._packet('NO_EVIDENCE','no_valid_event',phase,budget_chars)
        self._log(scope,session,question,action,packet['status'],event['id'] if event else '',
                  event['digest'] if event else '',origin)
        return {'action':action,'packet':packet}

    def mark_presented(self,scope,session,event_id):
        """可信调用方在真实展示唯一候选之后调用；不证明消息实际送达。"""
        f=self._focus(scope,session)
        if not f or f['mode']!='candidate' or f['event_id']!=event_id:
            raise MemoryFlowError('no uniquely proposed candidate')
        e=self._snapshot(scope,event_id)
        if not e or e['stale'] or e['digest']!=f['digest']:
            raise MemoryFlowError('candidate source changed')
        self._store(scope,session,'candidate',event_id,e['digest'],1)

    def handle(self,*,scope,session,origin,speaker,kind,content,recorded_at,
               occurred_at=None,budget_chars=900):
        ing=self.ingest(scope=scope,origin=origin,speaker=speaker,kind=kind,
              content=content,recorded_at=recorded_at,occurred_at=occurred_at)
        if speaker!='user':
            return {'ingest':ing,'retrieval':None,'reason':'assistant_is_not_user_query'}
        result=self.query(scope=scope,session=session,question=content,budget_chars=budget_chars,origin=origin)
        return {'ingest':ing,'retrieval':result}

    def turns(self,scope,session):
        return [dict(r) for r in self.db.execute('''SELECT origin,query_sha256,action,status,event_id,digest
         FROM e418_turns WHERE scope=? AND session=? ORDER BY serial''',(scope,session))]
