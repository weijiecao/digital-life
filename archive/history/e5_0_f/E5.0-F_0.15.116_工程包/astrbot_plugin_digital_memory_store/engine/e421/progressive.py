"""E4.21: incrementally recover candidate events as users add clues.

Not an AstrBot plugin, not a truth-verifier and not an autonomous reviewer.
The caller supplies an authenticated scope/session (E4.20 trust contract).
Unreviewed incoming clues are search input only: NEVER indexed as remembered
history, evidence, approval, or user facts. The human/UI must explicitly
acknowledge presenting one candidate before a user affirmation can confirm it.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import re
from ..e418.engine import UnifiedMemoryEngine, MemoryFlowError, _CONFIRM, _REJECT, _CLEAR, _FOLLOWUP

class ProgressiveError(ValueError):
    pass

class ProgressiveRecall:
    MAX_TURNS = 4
    MAX_CHARS = 400
    CLUE_TTL_SECONDS = 3600
    def __init__(self, engine: UnifiedMemoryEngine, candidate_search, *, max_turns=4):
        if not isinstance(engine,UnifiedMemoryEngine) or not callable(candidate_search):
            raise ProgressiveError('engine and candidate search required')
        if type(max_turns) is not int or not 1<=max_turns<=8:
            raise ProgressiveError('invalid max_turns')
        self.engine=engine
        self.db=engine.db
        self.search=candidate_search
        self.max_turns=max_turns
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS e421_clues (
           scope TEXT NOT NULL, session TEXT NOT NULL,
           seq INTEGER PRIMARY KEY AUTOINCREMENT,
           text TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_e421_clues ON e421_clues(scope,session,seq);
        CREATE TABLE IF NOT EXISTS e421_offer (
           scope TEXT NOT NULL, session TEXT NOT NULL,
           event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
           offered INTEGER NOT NULL DEFAULT 0,
           created_at TEXT NOT NULL,
           PRIMARY KEY(scope,session));
        CREATE TABLE IF NOT EXISTS e421_denied (
           scope TEXT NOT NULL, session TEXT NOT NULL, event_id TEXT NOT NULL,
           PRIMARY KEY(scope,session,event_id));
        ''')
        self.db.commit()

    def _require(self,scope,session,question=None):
        if not isinstance(scope,str) or not scope.strip() or len(scope)>256:
            raise ProgressiveError('trusted nonempty scope required')
        if not isinstance(session,str) or not session.strip() or len(session)>256:
            raise ProgressiveError('trusted nonempty session required')
        if question is not None and (not isinstance(question,str) or not question.strip() or len(question)>self.MAX_CHARS):
            raise ProgressiveError('question missing or too long')

    def _clear(self,scope,session,*,denied=False):
        with self.db:
            self.db.execute('DELETE FROM e421_clues WHERE scope=? AND session=?',(scope,session))
            self.db.execute('DELETE FROM e421_offer WHERE scope=? AND session=?',(scope,session))
            if denied:
                self.db.execute('DELETE FROM e421_denied WHERE scope=? AND session=?',(scope,session))

    def clear(self,scope,session):
        self._require(scope,session)
        self._clear(scope,session,denied=True)
        self.engine._store(scope,session,'cleared')
        return {'status':'CLEARED','may_quote_user':False,'may_assert_world_fact':False}

    def _clues(self,scope,session):
        cutoff=(datetime.now(timezone.utc)-timedelta(seconds=self.CLUE_TTL_SECONDS)).isoformat(timespec='seconds')
        with self.db:
            self.db.execute('DELETE FROM e421_clues WHERE scope=? AND session=? AND created_at<?',
                            (scope,session,cutoff))
        return [r['text'] for r in self.db.execute('''SELECT text FROM e421_clues
            WHERE scope=? AND session=? ORDER BY seq''',(scope,session))]

    def _append(self,scope,session,question):
        # A bounded, local, query-only cache; not part of source-of-truth memory.
        with self.db:
            self.db.execute('INSERT INTO e421_clues(scope,session,text,created_at) VALUES(?,?,?,?)',
                    (scope,session,question,datetime.now(timezone.utc).isoformat(timespec='seconds')))
            self.db.execute('''DELETE FROM e421_clues WHERE scope=? AND session=?
               AND seq NOT IN (SELECT seq FROM e421_clues WHERE scope=? AND session=?
               ORDER BY seq DESC LIMIT ?)''',(scope,session,scope,session,self.max_turns))
        return self._clues(scope,session)

    def _offer(self,scope,session):
        r=self.db.execute('SELECT * FROM e421_offer WHERE scope=? AND session=?',(scope,session)).fetchone()
        if not r:return None
        if datetime.fromisoformat(r['created_at']) < datetime.now(timezone.utc)-timedelta(seconds=self.CLUE_TTL_SECONDS):
            with self.db:self.db.execute('DELETE FROM e421_offer WHERE scope=? AND session=?',(scope,session))
            return None
        return dict(r)

    def _packet(self,status,reason,*,clues=0,associations=None,phase='all'):
        return {'status':status,'reason':reason,'clue_turns':clues,'phase':phase,
                'associations':associations or [],'fact_evidence':[],
                'context':'','may_quote_user':False,'may_assert_world_fact':False,
                'context_chars':0}

    def _external(self,scope,session,question,budget):
        # Only E4.18 can authorize contextual quotes after explicit title or
        # a confirmed, actually presented candidate.
        result=self.engine.query(scope=scope,session=session,question=question,
                                 budget_chars=budget)
        packet=result['packet']
        # Historical context must remain retrievable even if the user asked for
        # 'latest' while only an older phase is reviewed and a change is pending.
        # Do NOT move it to fact_evidence/current state.
        if (packet['status']=='PENDING_UPDATE' and not packet.get('historical_context')
                and packet.get('event_id')):
            all_history=self.engine._quote(scope,packet['event_id'],'all',budget)
            if all_history['status']=='PENDING_UPDATE':
                for key in ('historical_evidence','historical_context',
                            'historical_context_chars','historical_skipped_source_ids',
                            'may_quote_historical'):
                    packet[key]=all_history[key]
                packet['historical_fallback_phase']='all (history only, NOT current)'
        return {'route':'e418_confirmed_or_explicit','packet':result['packet'],
                'underlying_action':result['action'],'clue_turns':0}

    def mark_presented(self,scope,session,event_id):
        """Call ONLY after a trusted renderer actually presents the unique candidate.

        This method cannot verify delivery; UI / platform adapter must ensure it.
        """
        self._require(scope,session)
        if not isinstance(event_id,str):raise ProgressiveError('event ID required')
        offer=self._offer(scope,session)
        snap=self.engine._snapshot(scope,event_id)
        if (not offer or offer['event_id']!=event_id or not snap
            or snap['stale'] or snap['digest']!=offer['fingerprint']):
            raise ProgressiveError('candidate not uniquely offered or source changed')
        with self.db:
            self.db.execute('UPDATE e421_offer SET offered=1 WHERE scope=? AND session=?',
                            (scope,session))
        # E4.18 has a separate confirmation check; update its focus only after
        # verified presentation. The user still must explicitly confirm.
        self.engine._store(scope,session,'candidate',event_id,snap['digest'],offered=1)
        return {'status':'PRESENTED_AWAITING_USER_CONFIRMATION','event_id':event_id,
                'may_quote_user':False,'may_assert_world_fact':False}

    def turn(self, *,scope,session,question,budget_chars=900):
        self._require(scope,session,question)
        if type(budget_chars) is not int or budget_chars<1:
            raise ProgressiveError('invalid context budget')
        question=question.strip()
        events=self.engine._events(scope)
        titles=[e for e in events if e['title'] in question]
        # User explicitly points to a different event: do not stick to old focus.
        # Multiple full titles remain ambiguous as in E4.18.
        full=[e for e in titles if not re.search(r'(?:不是|并非|不指|别说)\s*'+re.escape(e['title']),question)]
        if _CLEAR.search(question):
            self._clear(scope,session,denied=True)
            return self._external(scope,session,question,budget_chars)
        if _REJECT.search(question):
            old=self._offer(scope,session)
            if old:
                with self.db:self.db.execute('INSERT OR IGNORE INTO e421_denied VALUES(?,?,?)',
                                             (scope,session,old['event_id']))
            self._clear(scope,session)
            return self._external(scope,session,question,budget_chars)
        if len(full)>=1:
            self._clear(scope,session,denied=True)
            return self._external(scope,session,question,budget_chars)
        if _CONFIRM.fullmatch(question):
            offer=self._offer(scope,session)
            if not offer or not offer['offered']:
                return {'route':'confirmation_not_authorized',
                        'packet':self._packet('UNCONFIRMED','candidate_not_presented'),
                        'clue_turns':len(self._clues(scope,session))}
            snap=self.engine._snapshot(scope,offer['event_id'])
            if not snap or snap['stale'] or snap['digest']!=offer['fingerprint']:
                self._clear(scope,session)
                self.engine._store(scope,session,'unresolved')
                return {'route':'stale_confirmation',
                        'packet':self._packet('STALE_REFERENCE','reviewed_sources_changed'),
                        'clue_turns':0}
            result=self._external(scope,session,question,budget_chars)
            self._clear(scope,session)
            # Confirmed E4.18 focus survives clearing E4.21 clue cache.
            return result
        focus=self.engine._focus(scope,session)
        if focus and focus['mode'] in ('confirmed','explicit') and _FOLLOWUP.search(question):
            return self._external(scope,session,question,budget_chars)
        # New turn invalidates any earlier offered candidate / old candidate focus.
        with self.db:
            self.db.execute('DELETE FROM e421_offer WHERE scope=? AND session=?',(scope,session))
        self.engine._store(scope,session,'unresolved')
        clues=self._append(scope,session,question)
        composite='\n'.join(clues)
        try:
            ids=self.search(scope,composite)
            if not isinstance(ids,(list,tuple)) or len(ids)>20:
                raise ProgressiveError('invalid search results')
            failure=None
        except Exception as exc:
            # No raw exception or untrusted model text enters prompt.
            ids=[]
            failure=type(exc).__name__
        denied={r['event_id'] for r in self.db.execute('''SELECT event_id FROM e421_denied
                           WHERE scope=? AND session=?''',(scope,session))}
        matches=[]
        for eid in dict.fromkeys(ids):
            if not isinstance(eid,str) or eid in denied:continue
            snap=self.engine._snapshot(scope,eid)
            if snap and not snap['stale']:
                matches.append({'event_id':eid,'title':snap['title'],
                                'source_fingerprint':snap['digest'],
                                'status':'ASSOCIATION_ONLY'})
            if len(matches)==3:break
        if len(matches)==1:
            c=matches[0]
            with self.db:
                self.db.execute('''INSERT INTO e421_offer(scope,session,event_id,fingerprint,offered,created_at)
                     VALUES(?,?,?,?,0,?) ON CONFLICT(scope,session) DO UPDATE SET
                     event_id=excluded.event_id,fingerprint=excluded.fingerprint,
                     offered=0,created_at=excluded.created_at''',
                     (scope,session,c['event_id'],c['source_fingerprint'],
                      datetime.now(timezone.utc).isoformat(timespec='seconds')))
        packet=self._packet('ASSOCIATIONS_ONLY' if matches else 'NO_EVIDENCE',
              'retrieval_failed_no_memory_evidence' if failure else
              'pending_user_confirmation' if matches else 'no_candidate_at_frozen_threshold',
              clues=len(clues),associations=matches)
        if failure:packet['retrieval_error_type']=failure
        return {'route':'progressive_candidates' if matches else 'more_clues_needed',
                'packet':packet,'clue_turns':len(clues),
                'search_input_chars':len(composite),
                'actual_history_quoted':False}
