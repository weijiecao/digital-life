"""引擎侧接入桥：输入层不承担事件创建、归并与事实判定。"""
from __future__ import annotations

import json
from astrbot_memory_input.native import Record, canonical, digest, Origin, parse_history
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.candidate_retriever import EventAnchor, HybridCandidateIndex
from v1_5_consolidator.relation_judge import JudgmentResult, RELATION_LABELS, SUBJECTS, REALITY
from v1_5_consolidator.resolver import ConstrainedResolver


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

    def accept(self, record: Record):
        # 接收端重新验证来源，避免调用方构造不一致的标准记录。
        if type(record.schema_version) is not int or record.schema_version != 1 or type(record.ordinal) is not int or record.ordinal < 0:
            raise ValueError('unsupported schema or ordinal')
        origin = Origin(**json.loads(record.origin_json))
        raw = json.loads(record.raw_json)
        if (record.scope_id != origin.scope_id or record.stream_id != origin.stream_id
                or digest(raw) != record.content_hash):
            raise ValueError('source identity/hash mismatch')
        parsed = parse_history([raw], origin)[0]
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


class NativeInputWorker:
    """judge(source, candidates, record, role_labelled_context) -> JudgmentResult.

    判别器由调用方注入；默认不调用模型，也不猜测主体/现实属性。
    暂缓结果按 scope epoch 和 judge_revision 缓存，避免无变化时重复判断。
    """
    def __init__(self, inbox, judge, encoder=None, judge_revision='v1'):
        self.inbox = inbox
        self.store = inbox.store
        self.judge = judge
        self.encoder = encoder
        self.judge_revision = judge_revision

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
        self.store.db.execute("UPDATE native_input_inbox SET state='DEFERRED' WHERE key=?", (key,))
        self.store.db.execute('INSERT OR REPLACE INTO native_input_attempt VALUES(?,?,?,?)',
                              (key, epoch, self.judge_revision, reason))

    def run_once(self, limit=100, retry_deferred=False):
        stats = dict(processed=0, created=0, attached=0, deferred=0, stale=0, errors=0)
        # 先过滤未变化的暂缓项，避免它们占满批次而饿死后续新输入。
        rows = self.store.db.execute('''SELECT i.key,i.payload,i.source_seq,a.epoch,a.judge_revision
            FROM native_input_inbox i LEFT JOIN native_input_attempt a ON a.key=i.key
            WHERE i.state IN ('QUEUED','DEFERRED') ORDER BY i.rowid''').fetchall()
        attempted = 0
        for key, payload, seq, last_epoch, last_revision in rows:
            record = Record(**json.loads(payload))
            scope = record.scope_id
            epoch = self.store.scope_epoch(scope)
            if not retry_deferred and last_epoch == epoch and last_revision == self.judge_revision:
                continue
            if attempted >= max(0, limit):
                break
            attempted += 1
            source = SourceEnvelope(scope, seq, record.content_hash, record.recorded_at or '', record.text)
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
            candidates = [by_id[c.event_id] for c in index.search(scope, record.text, k=20)]
            context = []
            for (prior,) in self.store.db.execute(
                    'SELECT payload FROM native_input_inbox WHERE scope_id=? ORDER BY rowid', (scope,)):
                item = Record(**json.loads(prior))
                if item.stream_id == record.stream_id and item.ordinal < record.ordinal:
                    context.append(item.as_dict())
            try:
                judgment = self.judge(source, candidates, record, context[-12:])
                reason = self._validate(judgment, seq, candidates)
            except Exception:
                # 单条判别失败不阻断其余输入；同轮不重试，下一轮可重试。
                stats['errors'] += 1
                continue
            with self.store.unit_of_work():
                state = self.store.db.execute('SELECT state FROM native_input_inbox WHERE key=?', (key,)).fetchone()[0]
                if state not in ('QUEUED', 'DEFERRED') or self.store.scope_epoch(scope) != epoch:
                    stats['stale'] += 1
                    continue
                if reason:
                    self._defer(key, epoch, reason)
                    stats['deferred'] += 1
                    continue
                resolver = ConstrainedResolver(self.store)
                if judgment.suggested_action == 'NEW_EVENT':
                    # 先创建再解析 DIFFERENT/RELATED，让不能合并关系也能绑定新事件。
                    event = self.store.create_event_from_source(scope, source, provenance=judgment.provenance.as_dict())
                    self.store.attach_source(scope, source, event, 'native-new:' + key)
                    if judgment.proposals:
                        resolver.resolve(scope, source, judgment)
                    outcome = 'CREATED'
                else:
                    resolution = resolver.resolve(scope, source, judgment)
                    if not (resolution.attached or resolution.already_attached):
                        self._defer(key, self.store.scope_epoch(scope), 'resolver_deferred')
                        stats['deferred'] += 1
                        continue
                    outcome = 'ATTACHED'
                if not self.store.record_receipt(scope, seq, outcome, source.source_hash):
                    raise ValueError('receipt conflict')
                self.store.db.execute("UPDATE native_input_inbox SET state='COMMITTED' WHERE key=?", (key,))
                self.store.bump_scope_epoch(scope)
                stats['created' if outcome == 'CREATED' else 'attached'] += 1
                stats['processed'] += 1
        return stats
