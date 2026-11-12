"""引擎侧接入桥：输入层不承担事件创建、归并与事实判定。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from astrbot_memory_input.native import Record, canonical, digest, Origin, parse_history
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.candidate_retriever import EventAnchor, HybridCandidateIndex
from v1_5_consolidator.relation_judge import JudgmentResult, RELATION_LABELS, SUBJECTS, REALITY
from v1_5_consolidator.resolver import ConstrainedResolver

# 连续失败后的最短退避；按 2**(attempts-1) 秒增长，封顶以便最终仍可重试。
MAX_BACKOFF_SECONDS = 300.0


def empty_stats() -> dict:
    return dict(processed=0, created=0, attached=0, deferred=0, stale=0, errors=0)


@dataclass
class InputTick:
    """一轮判别的调度状态；只在数据库线程里读写。"""
    limit: int
    retry_deferred: bool = False
    retry_limit: int = 0
    fresh: int = 0
    retries: int = 0
    seen: set = field(default_factory=set)

    def slots_left(self) -> int:
        return max(0, self.limit - self.fresh - self.retries)


class NativeInputInbox:
    def __init__(self, store):
        self.store = store
        with store.unit_of_work():
            store.db.execute('''CREATE TABLE IF NOT EXISTS native_input_inbox(
              key TEXT PRIMARY KEY, content_hash TEXT NOT NULL, scope_id TEXT NOT NULL,
              source_seq INTEGER, payload TEXT NOT NULL, state TEXT NOT NULL,
              UNIQUE(scope_id,source_seq))''')
            store.db.execute('''CREATE TABLE IF NOT EXISTS native_input_attempt(
              key TEXT PRIMARY KEY, epoch INTEGER NOT NULL, judge_revision TEXT NOT NULL,
              reason TEXT NOT NULL)''')
            columns = {row[1] for row in store.db.execute(
                'PRAGMA table_info(native_input_attempt)')}
            if 'attempts' not in columns:
                store.db.execute('ALTER TABLE native_input_attempt ADD COLUMN '
                                 'attempts INTEGER NOT NULL DEFAULT 0')
            if 'next_attempt_at' not in columns:
                store.db.execute('ALTER TABLE native_input_attempt ADD COLUMN '
                                 'next_attempt_at REAL NOT NULL DEFAULT 0')

    def accept(self, record: Record):
        # 接收端重新验证来源，避免调用方构造不一致的标准记录。
        if type(record.schema_version) is not int or record.schema_version != 1 or type(record.ordinal) is not int or record.ordinal < 0:
            raise ValueError('unsupported schema or ordinal')
        origin = Origin(**json.loads(record.origin_json))
        raw = json.loads(record.raw_json)
        if (record.scope_id != origin.scope_id or record.stream_id != origin.stream_id
                or digest(raw) != record.content_hash):
            raise ValueError('source identity/hash mismatch')
        parsed = parse_history([raw], origin)
        if not parsed:
            # 内部段（例如 _checkpoint）不是用户事实，不能作为来源入库。
            raise ValueError('internal segment is not a source record')
        parsed = parsed[0]
        expected_key = digest([origin.stream_id, record.identity_mode,
                               record.native_id if record.native_id is not None else record.ordinal])
        fields = ('role', 'text', 'recorded_at', 'native_id', 'identity_mode')
        if record.key != expected_key or any(getattr(parsed, f) != getattr(record, f) for f in fields):
            raise ValueError('normalized source mismatch')
        with self.store.unit_of_work():
            old = self.store.db.execute(
                'SELECT content_hash FROM native_input_inbox WHERE key=?', (record.key,)).fetchone()
            if old:
                if old[0] != record.content_hash:
                    raise ValueError('source identity conflict')
            else:
                seq = None
                # 所有角色原样归档；明确标为摘要的内容也只归档，不升格为用户事实。
                is_message = raw.get('source_kind', 'message') == 'message' and not raw.get('is_summary', False)
                state = 'QUEUED' if record.role == 'user' and record.text.strip() and is_message else 'ARCHIVED'
                if state == 'QUEUED':
                    maxima = []
                    for table in ('source_ref', 'source_receipt', 'resolution_pending', 'native_input_inbox'):
                        maxima.append(self.store.db.execute(
                            f'SELECT COALESCE(MAX(source_seq),0) FROM {table} WHERE scope_id=?',
                            (record.scope_id,)).fetchone()[0])
                    seq = max(maxima) + 1
                    self.store.source_ref_upsert(record.scope_id, seq, record.content_hash, record.recorded_at or '')
                self.store.db.execute('INSERT INTO native_input_inbox VALUES(?,?,?,?,?,?)',
                                     (record.key, record.content_hash, record.scope_id, seq,
                                      canonical(record.as_dict()), state))
                # 新上下文到达会使暂缓记录在下一轮获得重新判断机会。
                self.store.bump_scope_epoch(record.scope_id)
        return {'key': record.key, 'content_hash': record.content_hash}

    def counts(self):
        """各处理状态的条数；供宿主观察积压，不参与判定。"""
        rows = self.store.db.execute(
            'SELECT state,COUNT(*) FROM native_input_inbox GROUP BY state').fetchall()
        return {row[0]: row[1] for row in rows}

    def backlog(self) -> dict:
        """未决条数与失败重试明细，供宿主健康状态使用。"""
        rows = self.store.db.execute('''SELECT i.state,COUNT(*),
              SUM(CASE WHEN COALESCE(a.attempts,0)>0 THEN 1 ELSE 0 END),
              SUM(CASE WHEN COALESCE(a.next_attempt_at,0)>? THEN 1 ELSE 0 END)
            FROM native_input_inbox i LEFT JOIN native_input_attempt a ON a.key=i.key
            WHERE i.state IN ('QUEUED','DEFERRED') GROUP BY i.state''', (time.time(),)).fetchall()
        pending = {'QUEUED': 0, 'DEFERRED': 0, 'retrying': 0, 'waiting_backoff': 0}
        for state, count, retrying, waiting in rows:
            pending[state] = int(count or 0)
            pending['retrying'] += int(retrying or 0)
            pending['waiting_backoff'] += int(waiting or 0)
        return pending


class NativeInputWorker:
    """judge(source, candidates, record, role_labelled_context) -> JudgmentResult.

    判别器由调用方注入；默认不调用模型，也不猜测主体/现实属性。
    暂缓结果按 scope epoch 和 judge_revision 缓存，避免无变化时重复判断。

    调度分为“准备（数据库线程）→ 判别（模型线程）→ 提交（数据库线程）”三段，
    因此一次慢模型调用不会占用聊天路径共用的数据库线程。
    判别器若同时提供 ``prepare`` / ``generate`` / ``complete``，准备阶段会先落
    判别请求快照与审计，模型阶段只做网络等待，提交阶段再短事务复验。
    """

    def __init__(self, inbox, judge, encoder=None, judge_revision='v1'):
        self.inbox = inbox
        self.store = inbox.store
        self.judge = judge
        self.encoder = encoder
        self.judge_revision = judge_revision

    # -- 校验 -----------------------------------------------------------

    def _validate(self, judgment, seq, candidates):
        if not isinstance(judgment, JudgmentResult) or judgment.source_seq != seq or judgment.issues:
            return 'invalid_judgment'
        allowed = {c['event_id'] for c in candidates}
        ids = [p.candidate_event_id for p in judgment.proposals]
        if set(ids) != allowed or len(ids) != len(set(ids)):
            return 'candidate_coverage'
        if any(p.label not in RELATION_LABELS for p in judgment.proposals):
            return 'invalid_label'
        provenance = judgment.provenance
        if (not judgment.provenance_present or provenance.narrator != 'user'
                or provenance.subject not in SUBJECTS or provenance.reality_status not in REALITY
                or (provenance.story_id is not None and not isinstance(provenance.story_id, str))):
            return 'invalid_provenance'
        same = [p.candidate_event_id for p in judgment.proposals if p.label == 'SAME_EVENT']
        if judgment.suggested_action == 'NEW_EVENT':
            if same or any(p.label == 'UNCERTAIN' for p in judgment.proposals) or judgment.target_event_id:
                return 'inconsistent_new_event'
        elif judgment.suggested_action == 'ATTACH_EXISTING':
            if len(same) != 1 or judgment.target_event_id != same[0]:
                return 'inconsistent_attach'
            if any(p.label == 'UNCERTAIN' for p in judgment.proposals):
                return 'uncertain_candidate'
        else:
            return 'defer_or_invalid_action'
        return None

    def _defer(self, key, epoch, reason):
        """暂缓：由 epoch / judge_revision 变化触发复判，不算失败重试。"""
        with self.store._write():
            self.store.db.execute(
                "UPDATE native_input_inbox SET state='DEFERRED' WHERE key=?", (key,))
            self.store.db.execute(
                'INSERT OR REPLACE INTO native_input_attempt'
                '(key,epoch,judge_revision,reason,attempts,next_attempt_at) VALUES(?,?,?,?,0,0)',
                (key, epoch, self.judge_revision, reason))

    def _record_failure(self, key, attempts, reason, backoff=True):
        """持久化失败次数、下次尝试时间与错误原因；不改变 inbox 状态。

        模型/判别失败按指数退避，避免持续失败的老记录每轮都占住名额；
        提交阶段失败（数据库临时错误）允许下一轮立即重试，但同样受重试配额限制。
        """
        delay = min(MAX_BACKOFF_SECONDS, float(2 ** min(max(0, attempts - 1), 8))) if backoff else 0.0
        with self.store._write():
            self.store.db.execute(
                'INSERT INTO native_input_attempt'
                '(key,epoch,judge_revision,reason,attempts,next_attempt_at) VALUES(?,?,?,?,?,?) '
                'ON CONFLICT(key) DO UPDATE SET epoch=excluded.epoch,'
                'judge_revision=excluded.judge_revision,reason=excluded.reason,'
                'attempts=excluded.attempts,next_attempt_at=excluded.next_attempt_at',
                (key, -1, '', str(reason)[:200], attempts, time.time() + delay))

    # -- 调度 -----------------------------------------------------------

    def start_tick(self, limit=100, retry_deferred=False, retry_limit=None) -> InputTick:
        limit = max(0, int(limit))
        if retry_limit is None:
            # 周期全量重试时把本轮配额都留给重试；否则重试只占一小部分配额。
            retry_limit = limit if retry_deferred else (max(1, limit // 4) if limit else 0)
        return InputTick(limit=limit, retry_deferred=bool(retry_deferred),
                         retry_limit=max(0, int(retry_limit)))

    def _candidates(self, scope, text):
        raw_candidates = self.store.db.execute('''SELECT DISTINCT c.event_id,c.title,c.event_type,
            c.narrator,c.subject,c.reality_status,c.story_id,e.quote
          FROM event_candidate c JOIN event_evidence e ON e.event_id=c.event_id AND e.rev=c.current_revision
          WHERE c.scope_id=? AND e.scope_id=c.scope_id AND c.state!='withdrawn' AND e.valid=1
          AND NOT EXISTS (SELECT 1 FROM resolution_pending p WHERE p.scope_id=c.scope_id
            AND p.source_seq=e.source_seq AND p.status='PENDING')''', (scope,)).fetchall()
        index = HybridCandidateIndex(self.encoder)
        by_id = {}
        for eid, title, etype, narrator, subject, reality, story, quote in raw_candidates:
            candidate = by_id.setdefault(eid, {
                'event_id': eid, 'title': title, 'event_type': etype,
                'evidence': [], 'provenance': {
                    'narrator': narrator, 'subject': subject,
                    'reality_status': reality, 'story_id': story}})
            candidate['evidence'].append(quote)
        for eid, candidate in by_id.items():
            candidate['anchor_text'] = '\n'.join(candidate['evidence'])
            index.add(EventAnchor(eid, scope, canonical_title=candidate['title'],
                                  anchor_text=candidate['anchor_text']))
        return [by_id[c.event_id] for c in index.search(scope, text, k=20)]

    def prepare_one(self, tick: InputTick):
        """数据库线程：选出下一条可处理原话并准备好判别输入；不调用模型。"""
        if tick is None or tick.slots_left() <= 0:
            return None
        order = ('CASE WHEN i.state=\'DEFERRED\' THEN 0 WHEN COALESCE(a.attempts,0)=0 THEN 1 '
                 'ELSE 2 END' if tick.retry_deferred else
                 'CASE WHEN i.state=\'QUEUED\' AND COALESCE(a.attempts,0)=0 THEN 0 '
                 'WHEN i.state=\'DEFERRED\' THEN 2 ELSE 1 END')
        rows = self.store.db.execute(
            'SELECT i.key,i.payload,i.source_seq,i.state,a.epoch,a.judge_revision,'
            'COALESCE(a.attempts,0),COALESCE(a.next_attempt_at,0) '
            'FROM native_input_inbox i LEFT JOIN native_input_attempt a ON a.key=i.key '
            "WHERE i.state IN ('QUEUED','DEFERRED') ORDER BY " + order +
            ', COALESCE(a.next_attempt_at,0), i.rowid').fetchall()
        now = time.time()
        for key, payload, seq, state, last_epoch, last_revision, attempts, next_at in rows:
            if key in tick.seen:
                continue
            attempts = int(attempts or 0)
            waiting = float(next_at or 0) > now
            retry = state == 'DEFERRED' or attempts > 0
            # 新任务与重试分开配额：持续失败的老记录不能吃掉整轮名额。
            if retry:
                if tick.retries >= tick.retry_limit:
                    continue
            elif tick.fresh >= tick.limit:
                continue
            decoded = json.loads(payload)
            if not tick.retry_deferred:
                if state == 'DEFERRED' and last_epoch == self.store.scope_epoch(
                        decoded['scope_id']) and last_revision == self.judge_revision:
                    continue
                if attempts and waiting:
                    # 退避未到期：本轮跳过，把名额留给其他原话。
                    continue
            record = Record(**decoded)
            scope = record.scope_id
            epoch = self.store.scope_epoch(scope)
            source = SourceEnvelope(scope, seq, record.content_hash,
                                    record.recorded_at or '', record.text)
            context = []
            for (prior,) in self.store.db.execute(
                    'SELECT payload FROM native_input_inbox WHERE scope_id=? ORDER BY rowid',
                    (scope,)):
                item = Record(**json.loads(prior))
                if item.stream_id == record.stream_id and item.ordinal < record.ordinal:
                    context.append(item.as_dict())
            candidates = self._candidates(scope, record.text)
            task = {'key': key, 'seq': seq, 'scope': scope, 'epoch': epoch,
                    'record': record, 'source': source, 'candidates': candidates,
                    'context': context[-12:], 'prepared': None, 'judgment': None,
                    'error': None}
            if callable(getattr(self.judge, 'prepare', None)) and callable(
                    getattr(self.judge, 'complete', None)):
                try:
                    prepared = self.judge.prepare(source, candidates, record, task['context'])
                except Exception as exc:  # 判别请求未能建立，按可重试错误处理
                    task['error'] = type(exc).__name__ + ': ' + str(exc)
                else:
                    if isinstance(prepared, JudgmentResult):
                        task['judgment'] = prepared
                    else:
                        task['prepared'] = prepared
            if task['error'] is None:
                if retry:
                    tick.retries += 1
                else:
                    tick.fresh += 1
                tick.seen.add(key)
            return task
        return None

    def execute(self, task):
        """模型线程：只做判别调用，不访问 sqlite。"""
        if not task or task.get('error') is not None or task.get('judgment') is not None:
            return task
        try:
            if task.get('prepared') is not None:
                result = self.judge.generate(task['prepared'])
                task['judgment'] = self.judge.complete(task['prepared'], result)
            else:
                task['judgment'] = self.judge(task['source'], task['candidates'],
                                              task['record'], task['context'])
        except Exception as exc:
            task['error'] = type(exc).__name__ + ': ' + str(exc)
        return task

    def commit_one(self, task) -> dict:
        """数据库线程：短事务复验后写入判别结果；返回本条统计。"""
        stats = empty_stats()
        if not task:
            return stats
        key = task['key']
        scope = task['scope']
        if task.get('error') is not None:
            attempts = self.store.db.execute(
                'SELECT attempts FROM native_input_attempt WHERE key=?', (key,)).fetchone()
            attempts = int(attempts[0] or 0) + 1 if attempts else 1
            with self.store.unit_of_work():
                self._record_failure(key, attempts, task['error'])
            stats['errors'] += 1
            return stats
        source = task['source']
        seq = task['seq']
        epoch = task['epoch']
        judgment = task.get('judgment')
        reason = self._validate(judgment, seq, task['candidates'])
        try:
            with self.store.unit_of_work():
                row = self.store.db.execute(
                    'SELECT state FROM native_input_inbox WHERE key=?', (key,)).fetchone()
                if row is None:
                    stats['stale'] += 1
                    return stats
                state = row[0]
                if state not in ('QUEUED', 'DEFERRED') or self.store.scope_epoch(scope) != epoch:
                    stats['stale'] += 1
                    return stats
                if reason:
                    self._defer(key, epoch, reason)
                    stats['deferred'] += 1
                    return stats
                resolver = ConstrainedResolver(self.store)
                if judgment.suggested_action == 'NEW_EVENT':
                    # 先创建再解析 DIFFERENT/RELATED，让不能合并关系也能绑定新事件。
                    event = self.store.create_event_from_source(
                        scope, source, provenance=judgment.provenance.as_dict())
                    self.store.attach_source(scope, source, event, 'native-new:' + key)
                    if judgment.proposals:
                        resolver.resolve(scope, source, judgment)
                    outcome = 'CREATED'
                else:
                    resolution = resolver.resolve(scope, source, judgment)
                    if not (resolution.attached or resolution.already_attached):
                        self._defer(key, self.store.scope_epoch(scope), 'resolver_deferred')
                        stats['deferred'] += 1
                        return stats
                    outcome = 'ATTACHED'
                if not self.store.record_receipt(scope, seq, outcome, source.source_hash):
                    raise ValueError('receipt conflict')
                self.store.db.execute(
                    "UPDATE native_input_inbox SET state='COMMITTED' WHERE key=?", (key,))
                self.store.bump_scope_epoch(scope)
        except Exception:
            # 提交阶段失败也要记住退避（事务已回滚，另起一条写入），
            # 不能让这条记录长期占住队首并挤掉后续原话。
            attempts = self.store.db.execute(
                'SELECT attempts FROM native_input_attempt WHERE key=?', (key,)).fetchone()
            attempts = int(attempts[0] or 0) + 1 if attempts else 1
            self._record_failure(key, attempts, 'commit_error', backoff=False)
            raise
        stats['processed'] += 1
        stats['created' if outcome == 'CREATED' else 'attached'] += 1
        return stats

    def run_once(self, limit=100, retry_deferred=False, retry_limit=None):
        """同步跑一轮：准备、判别、提交在同一线程完成（CLI 与测试用）。"""
        stats = empty_stats()
        tick = self.start_tick(limit, retry_deferred, retry_limit)
        for _ in range(tick.limit):
            task = self.prepare_one(tick)
            if task is None:
                break
            self.execute(task)
            for name, value in self.commit_one(task).items():
                stats[name] += value
        return stats
