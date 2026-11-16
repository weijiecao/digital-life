"""E4.17 / 离线记忆写入闭环。标准库实现，可在 Windows 10 / Python 3.12 使用。

安全边界：
- 所有输入原话写入独立的 MemoryCore 数据库；相同 origin 幂等，冲突直接拒绝。
- 自动匹配只产生 *待审核事件链接*，不构成事实证明；不猜测用户未说出的事实。
- 只有经审阅的用户原话可以被放到“有来源的用户陈述”上下文中。
- 回复来源不可以当作用户事实；模型推测不自动写回；修正不覆盖旧历史。
- 明确事件标签【事件：标题】是演示用的可信结构提示，不代表实现了自然语言事件生成。
- 用户/审核者身份由调用方传入；本模块不提供身份认证，不能直接公开为生产 API。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any
from ..lifemem.core import MemoryCore, MissingRecord, required

EVENT_TAG = re.compile(r'^\s*【事件[：:]([^】\n]{2,100})】')
PHASE_TERMS = {
    'earliest': ('最初', '原本', '起初', '一开始', '最早', '原计划'),
    'change': ('后来', '改成', '改为', '调整', '改到了', '变更'),
    'paused': ('暂停', '搁置', '中止'),
    'latest': ('最终', '最后', '已经', '完成了', '取消了', '结果是'),
}
RELATION_KINDS = ('corrects', 'supersedes', 'retracts', 'related')
PHASES = ('earliest', 'change', 'paused', 'latest', 'unknown')

class ReviewError(ValueError):
    pass


def _digest(value: str) -> str:
    return sha256(value.encode('utf-8')).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _phase_guess(text: str) -> tuple[str, str]:
    hits = [p for p, terms in PHASE_TERMS.items() if any(t in text for t in terms)]
    return (hits[0], 'lexical_hint_only') if len(hits) == 1 else ('unknown', 'multiple_or_no_time_hints')


class MemoryWriter:
    """提供新消息写入、待审事件链接、追溯修正以及精确原文读取。

    调用方需自己控制输入来源、身份和权限；写入绝不能指向 AstrBot 旧数据库。
    """
    def __init__(self, core: MemoryCore):
        self.core = core
        self.db = core.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS e417_ingest (
          scope TEXT NOT NULL, origin TEXT NOT NULL, message_id TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN
            ('pending_review','approved_link','rejected_link','assistant_excluded')),
          candidate_event_id TEXT, guess_phase TEXT NOT NULL,
          guess_basis TEXT NOT NULL, source_sha256 TEXT NOT NULL,
          reviewer TEXT, reviewed_at TEXT,
          PRIMARY KEY(scope,origin), UNIQUE(scope,message_id),
          FOREIGN KEY(scope,message_id) REFERENCES messages(scope,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS e417_phase (
          scope TEXT NOT NULL, message_id TEXT NOT NULL,
          event_id TEXT NOT NULL, phase TEXT NOT NULL,
          PRIMARY KEY(scope,message_id),
          FOREIGN KEY(scope,message_id) REFERENCES messages(scope,id) ON DELETE CASCADE,
          FOREIGN KEY(scope,event_id) REFERENCES events(scope,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS e417_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
          message_id TEXT NOT NULL, action TEXT NOT NULL,
          actor TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
        );
        ''')
        self.db.commit()

    def _event_by_title(self, scope: str, title: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM events WHERE scope=? AND title=? ORDER BY id', (scope, title))]

    def _events(self, scope: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM events WHERE scope=? ORDER BY length(title) DESC,id', (scope,))]

    def status(self, scope: str, origin: str) -> dict | None:
        row = self.db.execute('SELECT * FROM e417_ingest WHERE scope=? AND origin=?',
                              (scope,origin)).fetchone()
        return dict(row) if row else None

    def ingest(self, *, scope: str, origin: str, speaker: str, kind: str,
               content: str, recorded_at: str, occurred_at: str | None = None) -> dict:
        """完整保存真实传入的原文；不会基于语义相似度自动确认为事实。"""
        scope = required(scope, 'scope')
        origin = required(origin, 'origin')
        if not isinstance(content,str) or not content.strip():
            raise ValueError('content must not be empty')
        # core 先保存不可变原文；已有 origin 的不同内容会被严格拒绝。
        mid = self.core.append(scope=scope,origin=origin,speaker=speaker,kind=kind,
                               content=content,recorded_at=recorded_at,occurred_at=occurred_at)
        existing = self.status(scope,origin)
        if existing:
            return {'message_id':mid,'state':existing['state'],
                    'candidate_event_id':existing['candidate_event_id'],
                    'phase_hint':existing['guess_phase'],'idempotent':True}
        phase,basis=_phase_guess(content)
        candidate=None
        state='assistant_excluded' if speaker=='assistant' else 'pending_review'
        if speaker=='user':
            tag=EVENT_TAG.match(content)
            if tag:
                title=tag.group(1).strip()
                matches=self._event_by_title(scope,title)
                if len(matches)==1:
                    candidate=matches[0]['id']
                    basis+=';explicit_event_tag'
                elif not matches:
                    # 创建“事件名称容器”而非生成事实：原文仍是待审状态。
                    candidate=self.core.create_event(scope,title)
                    basis+=';new_event_title_tag'
                else:
                    basis+=';ambiguous_event_title'
            else:
                matches=[]
                for e in self._events(scope):
                    if e['title'] in content and not re.search(
                        r'(?:不是|并非|不是说|不指)\s*'+re.escape(e['title']),content):
                        matches.append(e)
                # 完整标题不重名、也没有两件同时匹配才提供候选。
                if len(matches)==1 and len(self._event_by_title(scope,matches[0]['title']))==1:
                    candidate=matches[0]['id']
                    basis+=';unique_verbatim_title'
                elif len(matches)>1:
                    basis+=';multiple_event_titles'
        with self.core.write_scope():
            self.db.execute('''INSERT INTO e417_ingest
                (scope,origin,message_id,state,candidate_event_id,guess_phase,guess_basis,source_sha256)
                VALUES (?,?,?,?,?,?,?,?)''',
                (scope,origin,mid,state,candidate,phase,basis,_digest(content)))
            self._audit(scope,mid,'INGEST','system',state+'; '+basis)
        return {'message_id':mid,'state':state,'candidate_event_id':candidate,
                'phase_hint':phase,'idempotent':False}

    def _audit(self,scope,mid,action,actor,detail):
        self.db.execute('INSERT INTO e417_audit(scope,message_id,action,actor,detail,created_at) '
                        'VALUES(?,?,?,?,?,?)',(scope,mid,action,actor,detail,_utc_now()))

    def review(self, *, scope: str, origin: str, decision: str,
               reviewer: str, event_id: str | None = None,
               phase: str | None = None, reason: str = '') -> dict:
        """审阅者确认“原话属于此事件”及其阶段；不是确认世界事实真伪。"""
        reviewer = required(reviewer,'reviewer')
        if decision not in ('approve','reject'):
            raise ReviewError('decision must be approve or reject')
        row=self.status(scope,origin)
        if not row:raise ReviewError('unknown origin')
        if row['state'] == 'assistant_excluded':
            raise ReviewError('assistant text cannot be promoted to user evidence')
        if row['state'] != 'pending_review':
            raise ReviewError('already reviewed; cannot silently rewrite approval')
        mid=row['message_id']
        source=self.core._get('messages',scope,mid)
        if source['speaker']!='user' or _digest(source['content'])!=row['source_sha256']:
            raise ReviewError('source changed or not user-provided')
        selected=event_id or row['candidate_event_id']
        selected_phase=phase if phase is not None else row['guess_phase']
        if decision=='approve':
            if not selected: raise ReviewError('explicit valid event link required')
            if selected_phase not in PHASES:raise ReviewError('invalid time phase')
            try:self.core._get('events',scope,selected)
            except MissingRecord as exc:raise ReviewError('unknown or cross-scope event') from exc
        with self.core.write_scope():
            if decision=='approve':
                self.db.execute('INSERT INTO membership VALUES(?,?,?) '
                                'ON CONFLICT(scope,event_id,message_id) DO NOTHING', (scope,selected,mid))
                self.db.execute('INSERT INTO e417_phase VALUES(?,?,?,?)',
                                (scope,mid,selected,selected_phase))
            self.db.execute('''UPDATE e417_ingest SET state=?,candidate_event_id=?,reviewer=?,reviewed_at=?
                 WHERE scope=? AND origin=?''',
                 ('approved_link' if decision=='approve' else 'rejected_link',
                  selected if decision=='approve' else row['candidate_event_id'],
                  reviewer,_utc_now(),scope,origin))
            self._audit(scope,mid,'APPROVE_LINK' if decision=='approve' else 'REJECT_LINK',
                        reviewer,('event='+str(selected)+'; phase='+str(selected_phase)+'; '+reason)[:500])
        return {'message_id':mid,'state':'approved_link' if decision=='approve' else 'rejected_link',
                'event_id':selected if decision=='approve' else None,
                'phase':selected_phase if decision=='approve' else None}

    def relate(self, *,scope:str,newer_origin:str,older_origin:str,kind:str,
               reviewer:str,reason:str='') -> None:
        """记录补充/修正关系，不会覆盖旧原文，也不将新文本隐式解释为已完成事实。"""
        if kind not in RELATION_KINDS:raise ReviewError('invalid relation kind')
        reviewer=required(reviewer,'reviewer')
        n=self.status(scope,newer_origin);o=self.status(scope,older_origin)
        if not n or not o or n['state']!='approved_link' or o['state']!='approved_link':
            raise ReviewError('both user sources must be reviewed')
        if n['candidate_event_id']!=o['candidate_event_id']:
            raise ReviewError('cross-event relation requires separate review process')
        m1=self.core._get('messages',scope,n['message_id'])
        m2=self.core._get('messages',scope,o['message_id'])
        if m1['recorded_at']<=m2['recorded_at']:
            raise ReviewError('newer must be chronologically later')
        if _digest(m1['content'])!=n['source_sha256'] or _digest(m2['content'])!=o['source_sha256']:
            raise ReviewError('source content changed')
        self.core.relate(scope,n['message_id'],o['message_id'],kind)
        with self.core.write_scope():
            self._audit(scope,n['message_id'],'RELATE_'+kind.upper(),reviewer,
                        ('older='+o['message_id']+'; '+reason)[:500])

    def context(self, *, scope:str,event_id:str,phase:str='all',budget_chars:int=1000) -> dict:
        """只返回经审阅链接的逐字用户陈述；严格不裁断证据，不判断世界真伪。"""
        if phase not in (*PHASES,'all'):raise ValueError('invalid phase')
        if type(budget_chars) is not int or budget_chars<1:raise ValueError('invalid budget')
        self.core._get('events',scope,event_id)
        rows=self.db.execute('''SELECT m.id,m.content,m.recorded_at,m.occurred_at,m.speaker,
                         p.phase,i.source_sha256 FROM messages m
                JOIN e417_ingest i ON i.scope=m.scope AND i.message_id=m.id
                JOIN e417_phase p ON p.scope=m.scope AND p.message_id=m.id
                WHERE m.scope=? AND p.event_id=? AND i.state='approved_link'
                AND m.speaker='user' AND (?='all' OR p.phase=?)
                ORDER BY m.recorded_at,m.id''',(scope,event_id,phase,phase)).fetchall()
        lines=[];provided=[];skipped=[];size=0;stale=[]
        for row in rows:
            if _digest(row['content'])!=row['source_sha256']:
                stale.append(row['id']);continue
            line=(f"[{row['id']} | recorded={row['recorded_at']} | phase={row['phase']} | "
                  f"user said] {row['content']}\n")
            if size+len(line)>budget_chars:
                skipped.append(row['id']);continue
            lines.append(line);size+=len(line)
            provided.append({'message_id':row['id'],'phase':row['phase'],
                             'recorded_at':row['recorded_at'],'occurred_at':row['occurred_at'],
                             'speaker':'user','verbatim':row['content']})
        status=('STALE_SOURCE' if stale else 'USER_QUOTE_AVAILABLE' if provided else
                'BUDGET_INSUFFICIENT' if skipped else 'NO_REVIEWED_EVIDENCE')
        return {'status':status,'text':''.join(lines),'evidence':provided,
                'skipped_ids':skipped,'stale_ids':stale,
                'may_quote_user':bool(provided) and not stale,
                'may_assert_world_fact':False, 'context_chars':size,
                'budget_chars':budget_chars,'phase':phase}

    def pending(self,scope:str) -> list[dict]:
        """每个用户隔离的待审队列，不将其混入可信上下文。"""
        rows=self.db.execute('''SELECT i.origin,i.message_id,i.candidate_event_id,
                   i.guess_phase,i.guess_basis,m.content FROM e417_ingest i
                   JOIN messages m ON m.scope=i.scope AND m.id=i.message_id
                   WHERE i.scope=? AND i.state='pending_review'
                   ORDER BY m.recorded_at,m.id''',(scope,)).fetchall()
        return [dict(r) for r in rows]

    def audit(self,scope:str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            'SELECT message_id,action,actor,detail,created_at FROM e417_audit WHERE scope=? ORDER BY id',
            (scope,))]
