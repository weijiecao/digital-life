"""Dynamic semantic candidates from REVIEWED, SOURCE-VERIFIED, USER messages.

No inferred facts and no autonomous review. Embedding weights are supplied by the caller;
this module works offline with an injected encoder and never downloads models.
SQLite cache is an acceleration structure, not a source of memory truth.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from math import sqrt, isfinite


class IndexSafetyError(ValueError):
    pass


def _unit(vector):
    if not isinstance(vector, (list,tuple)) or not vector:
        raise IndexSafetyError('embedding must be a nonempty list/tuple')
    if len(vector) > 16384:
        raise IndexSafetyError('unexpected vector dimension')
    try:
        numbers=[float(x) for x in vector]
    except (ValueError, TypeError, OverflowError) as exc:
        raise IndexSafetyError('invalid embedding entries') from exc
    if not all(isfinite(x) for x in numbers):
        raise IndexSafetyError('nonfinite embedding')
    norm=sqrt(sum(x*x for x in numbers))
    if not isfinite(norm) or norm < 1e-12:
        raise IndexSafetyError('zero-length embedding')
    return [round(x/norm, 12) for x in numbers]


def _snapshot_input(snapshot):
    # Only verbatim material approved and traceable to a source; avoid unsupervised
    # summaries of assistant output. A title alone is never sufficient for indexing.
    lines=[snapshot['title']]
    for row in snapshot['rows']:
        lines.append(f"[{row['phase']}] {row['content']}")
    return '\n'.join(lines)


class DynamicReviewedIndex:
    """Incrementally rebuild changed events from the UnifiedMemoryEngine DB.

    Use instance as `engine.candidate_fn` or `search(scope, question)`.
    Caller must supply a trusted `scope` derived from authenticated platform identity.
    Similarity is only a candidate rank; no world facts may be asserted.
    """
    def __init__(self, engine, embedder, *, min_cosine=0.55, limit=3):
        if (not isinstance(min_cosine,(float,int)) or isinstance(min_cosine,bool)
            or not isfinite(min_cosine) or not 0<=min_cosine<=1):
            raise IndexSafetyError('invalid cosine threshold')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise IndexSafetyError('invalid result limit')
        self.engine=engine
        self.db=engine.db
        self.embedder=embedder
        self.encoder_key=getattr(embedder,'model_key',None)
        if not isinstance(self.encoder_key,str) or not self.encoder_key.strip():
            raise IndexSafetyError('versioned model_key required')
        # 算法版本纳入缓存键，旧版平均向量不会被误认为完整的分片索引。
        self.model_key=self.encoder_key+':event-segment-max-v1'
        if not callable(getattr(embedder,'encode_documents',None)) or not callable(getattr(embedder,'encode_queries',None)):
            raise IndexSafetyError('both query and document encoder required')
        self.threshold=float(min_cosine)
        self.limit=limit
        self.last_diagnostics=[]
        self.last_sync={'indexed':0,'reused':0,'removed':0,'embedded':0,'unavailable':0}
        self._retry_offsets={}
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS e419_vectors (
          scope TEXT NOT NULL, event_id TEXT NOT NULL, model_key TEXT NOT NULL,
          fingerprint TEXT NOT NULL, vector_json TEXT NOT NULL,
          dimensions INTEGER NOT NULL, indexed_at TEXT NOT NULL,
          PRIMARY KEY(scope,event_id,model_key));
        CREATE INDEX IF NOT EXISTS idx_e419_scope_model ON e419_vectors(scope,model_key);
        CREATE TABLE IF NOT EXISTS e419_segment_vectors (
          scope TEXT NOT NULL, event_id TEXT NOT NULL, model_key TEXT NOT NULL,
          message_id TEXT NOT NULL, vector_json TEXT NOT NULL, dimensions INTEGER NOT NULL,
          PRIMARY KEY(scope,event_id,model_key,message_id));
        ''')
        columns={row['name'] for row in self.db.execute('PRAGMA table_info(e419_vectors)')}
        if 'segment_count' not in columns:
            self.db.execute('ALTER TABLE e419_vectors ADD COLUMN segment_count INTEGER NOT NULL DEFAULT 0')
        self.db.commit()

    def _check_model(self):
        if getattr(self.embedder,'model_key',None)!=self.encoder_key:
            raise IndexSafetyError('encoder model has changed, new adapter required')

    def _encode_event(self,snapshot):
        """完整事件不适合编码时按完整原话拆分，不能漏掉来源或静默截断。"""
        try:
            result=self.embedder.encode_documents([_snapshot_input(snapshot)])
        except ValueError:
            rows=snapshot['rows']
            if not 1<len(rows)<=100:raise
            pieces=[snapshot['title']+'\n['+r['phase']+'] '+r['content'] for r in rows]
            vectors=[]
            for start in range(0,len(pieces),20):
                batch=pieces[start:start+20]
                result=self.embedder.encode_documents(batch)
                if not isinstance(result,(list,tuple)) or len(result)!=len(batch):
                    raise IndexSafetyError('segment encoding shape mismatch')
                vectors.extend(_unit(vector) for vector in result)
            dim=len(vectors[0])
            if any(len(vector)!=dim for vector in vectors):
                raise IndexSafetyError('incompatible segment dimensions')
            return _unit([sum(vector[i] for vector in vectors) for i in range(dim)]),list(zip([r['id'] for r in rows],vectors))
        if not isinstance(result,(list,tuple)) or len(result)!=1:
            raise IndexSafetyError('document encoding shape mismatch')
        return _unit(result[0]),[]

    def synchronize(self,scope):
        """Ensure index matches the latest reviewed evidence; no pending source indexed.

        Snapshot changes (even relation-only changes) invalidate old vector entries.
        Stale/tampered approved sources are excluded until separate remedial review.
        """
        self._check_model()
        snapshots={}
        for event in self.engine._events(scope):
            s=self.engine._snapshot(scope,event['id'])
            if s and not s['stale'] and s['rows']:
                snapshots[s['id']]=s
        existing={r['event_id']:dict(r) for r in self.db.execute('''
            SELECT event_id,fingerprint,vector_json,dimensions
            FROM e419_vectors WHERE scope=? AND model_key=?''',(scope,self.model_key))}
        obsolete=sorted(set(existing)-set(snapshots))
        outdated=sorted(eid for eid,s in snapshots.items()
                        if eid not in existing or existing[eid]['fingerprint']!=s['digest'])
        # 先批量编码；单条过长等失败时逐事件隔离，结果仍一次性提交。
        vectors={};segments={};failed=set();segmented=0
        if outdated:
            try:
                encoded=self.embedder.encode_documents([_snapshot_input(snapshots[x]) for x in outdated])
                if not isinstance(encoded,(list,tuple)) or len(encoded)!=len(outdated):
                    raise IndexSafetyError('document encoding shape mismatch')
                vectors={eid:_unit(raw) for eid,raw in zip(outdated,encoded)}
            except Exception:
                # 每轮最多尝试 20 个，游标轮转，避免一个坏事件饿死后续事件。
                vectors={};failed=set(outdated)
                if scope not in self._retry_offsets and len(self._retry_offsets)>=256:
                    self._retry_offsets.pop(next(iter(self._retry_offsets)))
                start=self._retry_offsets.get(scope,0)%len(outdated)
                ordered=outdated[start:]+outdated[:start]
                attempt=ordered[:20]
                self._retry_offsets[scope]=(start+len(attempt))%len(outdated)
                for eid in attempt:
                    try:
                        vectors[eid],segments[eid]=self._encode_event(snapshots[eid])
                        segmented+=int(bool(segments[eid]))
                        failed.discard(eid)
                    except Exception:
                        pass
        expected_dim=None
        for eid in sorted(snapshots):
            if eid in failed:continue
            if eid in vectors:
                v=vectors[eid]
            else:
                try:
                    v=json.loads(existing[eid]['vector_json'])
                except (TypeError,ValueError) as exc:
                    raise IndexSafetyError('stored vector damaged') from exc
                if not isinstance(v,list) or len(v)!=existing[eid]['dimensions'] or _unit(v) is None:
                    raise IndexSafetyError('invalid cached vector')
            if expected_dim is None:expected_dim=len(v)
            elif expected_dim!=len(v):raise IndexSafetyError('incompatible vector dimensions')
        timestamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.db:
            # 已失败的旧向量可能对应过时原话，必须一并删除，不能回退使用。
            for eid in sorted(set(obsolete)|failed):
                self.db.execute('DELETE FROM e419_segment_vectors WHERE scope=? AND event_id=? AND model_key=?',
                                (scope,eid,self.model_key))
                self.db.execute('DELETE FROM e419_vectors WHERE scope=? AND event_id=? AND model_key=?',
                                (scope,eid,self.model_key))
            for eid in sorted(vectors):
                self.db.execute('DELETE FROM e419_segment_vectors WHERE scope=? AND event_id=? AND model_key=?',
                                (scope,eid,self.model_key))
                parts=segments.get(eid,[])
                self.db.execute('''INSERT INTO e419_vectors
                    (scope,event_id,model_key,fingerprint,vector_json,dimensions,indexed_at,segment_count)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(scope,event_id,model_key) DO UPDATE SET
                    fingerprint=excluded.fingerprint, vector_json=excluded.vector_json,
                    dimensions=excluded.dimensions,indexed_at=excluded.indexed_at,segment_count=excluded.segment_count''',
                    (scope,eid,self.model_key,snapshots[eid]['digest'],
                     json.dumps(vectors[eid],separators=(',',':')),len(vectors[eid]),timestamp,len(parts)))
                self.db.executemany('''INSERT INTO e419_segment_vectors
                    (scope,event_id,model_key,message_id,vector_json,dimensions) VALUES(?,?,?,?,?,?)''',
                    [(scope,eid,self.model_key,mid,json.dumps(v,separators=(',',':')),len(v)) for mid,v in parts])
        self.last_sync={'indexed':len(snapshots)-len(failed),'reused':len(snapshots)-len(outdated),
                        'removed':len(set(obsolete)|(failed & set(existing))),
                        'embedded':len(vectors),'unavailable':len(failed),'segmented':segmented}
        return dict(self.last_sync)

    def search(self,scope,question):
        if not isinstance(question,str) or not question.strip():
            raise IndexSafetyError('nonempty query required')
        self.synchronize(scope)
        rows=list(self.db.execute('''SELECT event_id,vector_json,dimensions,segment_count FROM e419_vectors
           WHERE scope=? AND model_key=? ORDER BY event_id''',(scope,self.model_key)))
        if not rows:
            self.last_diagnostics=[]
            return []
        encoded=self.embedder.encode_queries([question])
        if not isinstance(encoded,(list,tuple)) or len(encoded)!=1:
            raise IndexSafetyError('query encoder shape mismatch')
        q=_unit(encoded[0]);scores=[]
        parts_by_event={}
        for part in self.db.execute('''SELECT event_id,vector_json,dimensions FROM e419_segment_vectors
                                     WHERE scope=? AND model_key=?''',(scope,self.model_key)):
            parts_by_event.setdefault(part['event_id'],[]).append(part)
        for row in rows:
            v=json.loads(row['vector_json'])
            if len(v)!=len(q) or len(v)!=row['dimensions']:
                raise IndexSafetyError('stored vector dimension mismatch')
            score=sum(a*b for a,b in zip(_unit(v),q))
            parts=parts_by_event.get(row['event_id'],[])
            if len(parts)!=row['segment_count']:raise IndexSafetyError('segment index incomplete')
            if parts:
                values=[]
                for part in parts:
                    vector=json.loads(part['vector_json'])
                    if len(vector)!=len(q) or len(vector)!=part['dimensions']:raise IndexSafetyError('segment dimension mismatch')
                    values.append(sum(a*b for a,b in zip(_unit(vector),q)))
                score=max(values)
            scores.append((score,row['event_id']))
        scores.sort(key=lambda x:(-x[0],x[1]))
        self.last_diagnostics=[{'event_id':eid,'cosine':round(score,4)} for score,eid in scores[:self.limit]]
        return [eid for score,eid in scores[:self.limit] if score>=self.threshold]

    __call__=search


class SafeCandidateAdapter:
    """Semantic retrieval must not bring down a conversation if optional BGE fails.

    A fallback ONLY returns lexical association candidates. It never authorizes
    factual quotes unless the normal E4.18 referent confirmation gate is met.
    """
    def __init__(self,index):
        self.index=index
        self.last_used_fallback=False
        self.last_error=''

    def __call__(self,scope,question):
        self.last_used_fallback=False
        self.last_error=''
        try:
            return self.index.search(scope,question)
        except Exception as exc:
            # Errors from optional local embedding (model version, tokenizer,
            # disk, dimensions, even Python model loading) never suppress
            # exact-source E4.18 retrieval. The error is observable, not silent.
            self.last_used_fallback=True
            self.last_error=f'{type(exc).__name__}: {exc}'[:300]
            ranked=[]
            for event in self.index.engine._events(scope):
                title=event['title']
                snap=self.index.engine._snapshot(scope,event['id'])
                if snap is None or snap['stale']:
                    continue
                overlap={title[j:j+2] for j in range(len(title)-1)}
                n=sum(part in question for part in overlap if part not in ('的','那个','这个'))
                if n:ranked.append((-n,event['id']))
            return [eid for _,eid in sorted(ranked)[:self.index.limit]]


def attach_dynamic_index(engine,embedder,*,min_cosine=0.55,limit=3):
    index=DynamicReviewedIndex(engine,embedder,min_cosine=min_cosine,limit=limit)
    # A failed optional model cannot make the approved-memory engine unavailable.
    engine.candidate_fn=SafeCandidateAdapter(index)
    return index
