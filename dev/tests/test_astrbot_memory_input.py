"""固定判别器验证接入协议，不代表真实模型的语义准确率。"""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from astrbot_memory_input import Origin, ImportJournal, ImportConflict, InputError, parse_history
from astrbot_memory_input.native import Record
from memory_engine.native_input import NativeInputInbox, NativeInputWorker
from v1_5_consolidator.store import V15Store
from v1_5_consolidator.contracts import NarrativeProvenance
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal

ORIGIN = Origin('qq', 'bot', 'u1', 'p1', 'c1')
REAL = NarrativeProvenance('user', 'user', 'real')
def msg(text='黄山旅行', role='user', **kw): return dict(role=role, content=text, **kw)
def new_judge(s, c, r, ctx):
    return JudgmentResult(s.source_seq, [RelationProposal(x['event_id'], 'DIFFERENT_EVENT') for x in c], suggested_action='NEW_EVENT', provenance=REAL)
def same_judge(s, c, r, ctx):
    return JudgmentResult(s.source_seq, [RelationProposal(x['event_id'], 'SAME_EVENT') for x in c], suggested_action='ATTACH_EXISTING', target_event_id=c[0]['event_id'], provenance=REAL)

class NativeInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.journal = ImportJournal(self.path / 'input.db'); self.addCleanup(self.journal.close)
        self.store = V15Store(self.path / 'engine.db'); self.addCleanup(self.store.close)
        self.inbox = NativeInputInbox(self.store)
    def ingest(self, history, origin=ORIGIN):
        self.journal.stage(history, origin); return self.journal.deliver(self.inbox)
    def count(self, table): return self.store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
    def test_native_wrappers_and_raw_fields(self):
        raw = msg(timestamp=123, extension={'x': 7})
        for p in ([raw], json.dumps([raw]), {'history': json.dumps([raw]), 'cid': 'c1'}):
            r = parse_history(p, ORIGIN)[0]
            self.assertEqual(json.loads(r.raw_json), raw); self.assertEqual(r.recorded_at, '123')
    def test_multimodal_raw_preserved(self):
        raw = msg([{'type': 'text', 'text': '海边'}, {'type': 'image_url', 'image_url': {'url': 'https://invalid/image'}}])
        r = parse_history([raw], ORIGIN)[0]
        self.assertEqual(r.text, '海边'); self.assertEqual(json.loads(r.raw_json), raw)
    def test_missing_metadata_not_inferred(self):
        r = parse_history([msg()], ORIGIN)[0]
        self.assertIsNone(r.recorded_at); self.assertNotIn('subject', r.as_dict())
    def test_invalid_inputs(self):
        bad = [[msg(role='mystery')], [msg(float('nan'))], [msg(timestamp=True)], [msg(message_id=True)], [msg([{'type': 'text', 'text': 1}])], [msg(), msg(id='x')], [msg(id='x'), msg(id='x')]]
        for history in bad:
            with self.subTest(history=history), self.assertRaises(InputError): parse_history(history, ORIGIN)
    def test_origin_mismatch(self):
        with self.assertRaises(InputError): parse_history({'history': [msg()], 'cid': 'wrong'}, ORIGIN)
        with self.assertRaises(InputError): Origin('qq', '', 'u', 'p', 'c')
    def test_prefix_append_repeat(self):
        h = [msg(), msg('好', 'assistant')]
        self.assertEqual(self.journal.stage(h, ORIGIN)['new'], 2)
        self.assertEqual(self.journal.stage(h, ORIGIN)['duplicates'], 2)
        self.assertEqual(self.journal.stage(h+[msg('继续')], ORIGIN)['new'], 1)
    def test_prefix_conflicts_atomic(self):
        h = [msg('a'), msg('b')]; self.journal.stage(h, ORIGIN)
        for changed in (h[:1], h[::-1], [msg('changed'), msg('b'), msg('new')]):
            with self.assertRaises(ImportConflict): self.journal.stage(changed, ORIGIN)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM native_record').fetchone()[0], 2)
        self.assertEqual(self.journal.db.execute("SELECT COUNT(*) FROM import_attempt WHERE status='REJECTED'").fetchone()[0], 3)
    def test_stable_id_delta_order(self):
        self.journal.stage([msg('a', id='a')], ORIGIN)
        self.journal.stage([msg('b', id='b')], ORIGIN)
        self.journal.stage([msg('b', id='b'), msg('a', id='a')], ORIGIN)
        rows = [Record(**json.loads(r[0])) for r in self.journal.db.execute('SELECT payload FROM native_record ORDER BY rowid')]
        self.assertEqual([(r.native_id,r.ordinal) for r in rows], [('a',0),('b',1)])
    def test_stable_id_edit_atomic(self):
        self.journal.stage([msg('a', id='1')], ORIGIN)
        with self.assertRaises(ImportConflict): self.journal.stage([msg('new', id='2'), msg('changed', id='1')], ORIGIN)
        self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM native_record').fetchone()[0], 1)
    def test_mode_change(self):
        self.journal.stage([msg()], ORIGIN)
        with self.assertRaises(ImportConflict): self.journal.stage([msg(id='1')], ORIGIN)
    def test_ack_mismatch(self):
        self.journal.stage([msg()], ORIGIN)
        class BadSink:
            def accept(self, r): return {}
        with self.assertRaises(ImportConflict): self.journal.deliver(BadSink())
        self.assertEqual(self.journal.db.execute('SELECT delivered FROM native_record').fetchone()[0], 0)
    def test_replay_after_commit_before_ack(self):
        self.journal.stage([msg()], ORIGIN); inbox = self.inbox
        class Crash:
            def accept(self, r):
                inbox.accept(r); raise RuntimeError('after commit')
        with self.assertRaises(RuntimeError): self.journal.deliver(Crash())
        reopened = ImportJournal(self.path/'input.db')
        try: self.assertEqual(reopened.deliver(self.inbox)['acknowledged'], 1)
        finally: reopened.close()
        self.assertEqual(self.count('native_input_inbox'), 1); self.assertEqual(self.count('source_ref'), 1)
    def test_roles_summaries_empty_archived(self):
        h = [msg(role=r) for r in ('assistant','tool','system','developer','function')]
        h += [msg(''), msg(source_kind='summary'), msg(is_summary=True), msg()]
        self.ingest(h)
        self.assertEqual(self.count('native_input_inbox'), 9); self.assertEqual(self.count('source_ref'), 1)
        self.assertEqual(NativeInputWorker(self.inbox,new_judge).run_once()['created'], 1)
    def test_forged_record(self):
        r = parse_history([msg()],ORIGIN)[0]
        for forged in (replace(r,text='伪造'),replace(r,scope_id='other'),replace(r,ordinal=-1),replace(r,schema_version=2)):
            with self.assertRaises(ValueError): self.inbox.accept(forged)
        self.assertEqual(self.count('native_input_inbox'),0)
    def test_scope_isolation(self):
        self.assertEqual(ORIGIN.scope_id,replace(ORIGIN,conversation='c2').scope_id)
        self.assertNotEqual(ORIGIN.stream_id,replace(ORIGIN,conversation='c2').stream_id)
        for f in ('user','bot','platform','persona'): self.assertNotEqual(ORIGIN.scope_id,replace(ORIGIN,**{f:'other'}).scope_id)
        self.ingest([msg()]); self.ingest([msg()],replace(ORIGIN,user='u2')); seen=[]
        def judge(s,c,r,ctx): seen.append(len(c)); return new_judge(s,c,r,ctx)
        self.assertEqual(NativeInputWorker(self.inbox,judge).run_once()['created'],2); self.assertEqual(seen,[0,0])
    def test_create_attach_idempotent(self):
        self.ingest([msg()]); self.assertEqual(NativeInputWorker(self.inbox,new_judge).run_once()['created'],1)
        self.ingest([msg(),msg('黄山旅行第二天')]); w=NativeInputWorker(self.inbox,same_judge)
        self.assertEqual(w.run_once()['attached'],1); self.assertEqual(w.run_once()['processed'],0)
        self.assertEqual(self.count('event_candidate'),1); self.assertEqual(self.count('source_receipt'),2)
    def test_different_persists_cannot_link(self):
        self.ingest([msg(),msg('另一次黄山旅行')])
        self.assertEqual(NativeInputWorker(self.inbox,new_judge).run_once()['created'],2)
        ids=[r[0] for r in self.store.db.execute('SELECT event_id FROM event_candidate')]
        self.assertTrue(self.store.cannot_link_exists(ORIGIN.scope_id,*ids))
    def test_context_roles_no_future(self):
        self.ingest([msg('建议','assistant'),msg(),msg('未来','assistant')]); captured=[]
        def judge(s,c,r,ctx): captured.extend(ctx); return new_judge(s,c,r,ctx)
        NativeInputWorker(self.inbox,judge).run_once()
        self.assertEqual([(r['role'],r['text']) for r in captured],[('assistant','建议')])
    def test_missing_provenance_deferred(self):
        self.ingest([msg()]); w=NativeInputWorker(self.inbox,lambda s,*a:JudgmentResult(s.source_seq,suggested_action='NEW_EVENT'))
        self.assertEqual(w.run_once()['deferred'],1); self.assertEqual(self.count('event_candidate'),0)
    def test_missing_candidates_deferred(self):
        self.ingest([msg()]); NativeInputWorker(self.inbox,new_judge).run_once()
        self.ingest([msg(),msg('黄山旅行第二天')])
        w=NativeInputWorker(self.inbox,lambda s,*a:JudgmentResult(s.source_seq,suggested_action='NEW_EVENT',provenance=REAL))
        self.assertEqual(w.run_once()['deferred'],1); self.assertEqual(self.count('event_candidate'),1)
    def test_story_guard(self):
        self.ingest([msg()])
        def fictional(s,c,r,ctx):
            j=new_judge(s,c,r,ctx); j.provenance=NarrativeProvenance('user','story_character','fictional','story1'); return j
        NativeInputWorker(self.inbox,fictional).run_once(); self.ingest([msg(),msg('黄山旅行第二天')])
        self.assertEqual(NativeInputWorker(self.inbox,same_judge).run_once()['deferred'],1)
        self.assertEqual(self.count('source_receipt'),1)
    def test_defer_cache_reconsideration(self):
        self.ingest([msg()]); calls=[]
        def judge(s,*a): calls.append(s.source_seq); return JudgmentResult(s.source_seq)
        w=NativeInputWorker(self.inbox,judge); w.run_once(); w.run_once(); self.assertEqual(len(calls),1)
        self.ingest([msg(),msg('补充','assistant')]); w.run_once(); self.assertEqual(len(calls),2)
        NativeInputWorker(self.inbox,judge,judge_revision='v2').run_once(); self.assertEqual(len(calls),3)
        w.run_once(retry_deferred=True); self.assertEqual(len(calls),4)
    def test_stale_epoch(self):
        self.ingest([msg()])
        def judge(s,c,r,ctx): self.store.bump_scope_epoch(s.scope_id); return new_judge(s,c,r,ctx)
        self.assertEqual(NativeInputWorker(self.inbox,judge).run_once()['stale'],1); self.assertEqual(self.count('event_candidate'),0)
    def test_competing_worker(self):
        self.ingest([msg()])
        def judge(s,c,r,ctx): NativeInputWorker(self.inbox,new_judge).run_once(); return new_judge(s,c,r,ctx)
        self.assertEqual(NativeInputWorker(self.inbox,judge).run_once()['stale'],1); self.assertEqual(self.count('event_candidate'),1)
    def test_receipt_failure_rollback(self):
        self.ingest([msg()])
        with patch.object(self.store,'record_receipt',return_value=False):
            with self.assertRaises(ValueError): NativeInputWorker(self.inbox,new_judge).run_once()
        self.assertEqual(self.count('event_candidate'),0); self.assertEqual(self.count('event_evidence'),0)
        self.assertEqual(NativeInputWorker(self.inbox,new_judge).run_once()['created'],1)
    def test_provider_failure_isolated(self):
        self.ingest([msg('a'),msg('b')])
        def judge(s,c,r,ctx):
            if r.text=='a': raise RuntimeError('provider unavailable')
            return new_judge(s,c,r,ctx)
        r=NativeInputWorker(self.inbox,judge).run_once(); self.assertEqual((r['errors'],r['created']),(1,1))
    def test_empty_initial_history_does_not_pin_identity_mode(self):
        self.journal.stage([],ORIGIN)
        self.assertEqual(self.journal.stage([msg(id='1')],ORIGIN)['new'],1)
    def test_import_to_existing_context_query(self):
        from memory_engine.engine import MemoryEngine
        from memory_engine.render import render_context
        self.ingest([msg('周六去青岛海边散步。')])
        NativeInputWorker(self.inbox,new_judge).run_once()
        packet=MemoryEngine(self.store).build_context(ORIGIN.scope_id,'青岛海边散步')
        self.assertEqual(len(packet['items']),1)
        self.assertIn('周六去青岛海边散步',render_context(packet))
        self.assertEqual(packet['items'][0]['admission']['verdict'],'SUPPORTED')

if __name__=='__main__': unittest.main()
