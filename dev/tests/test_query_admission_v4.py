"""End-to-end safety contracts, including mutations during admission/rendering."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from memory_engine.engine import MemoryEngine
from memory_engine import admission
from v1_5_consolidator.store import V15Store
from v1_5_consolidator.contracts import SourceEnvelope

class ConstantEncoder:
    def encode(self,texts):
        return [[1.0,0.0] for _ in texts]

def source(seq,text):
    return SourceEnvelope('s',seq,hashlib.sha256(text.encode()).hexdigest(),'t',text)

class TestQueryAdmission(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=V15Store(Path(self.temp.name)/'db')
        self.engine=MemoryEngine(self.store)
    def tearDown(self):
        self.store.close(); self.temp.cleanup()
    def ingest(self,text,seq=1,provenance=None):
        return self.engine.ingest('s',source(seq,text),provenance).event_id
    def test_missing_attributes_with_maximal_semantic_similarity(self):
        self.ingest('木桥市集在西入口集合。')
        e=MemoryEngine(self.store,encoder=ConstantEncoder())
        for q in ['木桥市集几点集合','木桥市集门票多少钱','木桥市集需要多久','为什么选木桥市集','木桥市集联系人号码是什么']:
            with self.subTest(query=q): self.assertEqual(e.build_context('s',q)['items'],[])
    def test_positive_attributes_and_exact_citations(self):
        text='木桥市集周六十点在西入口集合，门票50元，活动两小时，因为下雨而取消。'
        self.ingest(text)
        for q in ['木桥市集几点集合','木桥市集门票多少钱','木桥市集需要多久','为什么取消木桥市集']:
            with self.subTest(query=q):
                trace=self.engine.retrieve_with_audit('s',q)
                self.assertEqual(len(trace['items']),1)
                cert=trace['items'][0]['admission']; span=cert['supporting_spans'][0]
                self.assertEqual(text[span['start']:span['end']],span['text'])
                self.assertEqual(cert['query_hash'],admission.digest(q))
    def test_unknown_subject_not_assumed_self(self):
        self.ingest('周六去银行。')
        self.assertEqual(self.engine.retrieve('s','我周六去银行吗'),[])
    def test_family_metadata_is_not_specific_member(self):
        self.ingest('妈妈周日去银行。',provenance={'subject':'family','reality_status':'real'})
        self.assertEqual(self.engine.retrieve('s','爸爸周日去银行吗'),[])
        self.assertTrue(self.engine.retrieve('s','妈妈周日去银行吗'))
    def test_self_not_mother(self):
        self.ingest('我周日去银行。',provenance={'subject':'user','reality_status':'real'})
        self.assertEqual(self.engine.retrieve('s','妈妈周日去银行吗'),[])
    def test_same_theme_wrong_day(self):
        self.ingest('我周六去银行，上午十点。',1,{'subject':'user','reality_status':'real'})
        wrong=self.ingest('我周日去银行，上午十一点。',2,{'subject':'user','reality_status':'real'})
        items=self.engine.retrieve('s','我周六去银行几点')
        self.assertTrue(items);self.assertNotIn(wrong,[i['event_id'] for i in items])
    def test_weekend_is_not_thursday(self):
        self.ingest('故事角色阿岚周四去海边。',provenance={'subject':'story_character','reality_status':'fictional'})
        self.assertEqual(self.engine.retrieve('s','故事角色周末去海边哪里'),[])
    def test_summary_cannot_supply_missing_evidence(self):
        event=self.ingest('木桥市集在西入口集合。')
        with self.store._write():
            self.store.db.execute('UPDATE event_revision SET summary=? WHERE event_id=?',('木桥市集十点集合',event))
        self.assertEqual(self.engine.retrieve('s','木桥市集几点集合'),[])
    def test_multi_source_index_and_citations(self):
        event=self.ingest('木桥市集周六十点集合。')
        self.store.attach_source('s',source(2,'木桥市集门票50元。'),event,'test')
        e=MemoryEngine(self.store,encoder=ConstantEncoder())
        for q,seq in [('木桥市集几点集合',1),('木桥市集门票多少钱',2)]:
            cert=e.retrieve('s',q)[0]['admission']
            self.assertIn(seq,[r['source_seq'] for r in cert['supporting_spans']])
    def test_malformed_and_fabricated_certificates_abstain(self):
        self.ingest('木桥市集十点集合。');original=self.engine._admit
        def fake(q,s,score):
            d=original(q,s,score);d['supporting_spans'][0]['text']='伪造';return d
        for fn in [lambda *a:None, lambda *a:{'verdict':'SUPPORTED'}, fake]:
            self.engine._admit=fn
            self.assertEqual(self.engine.retrieve('s','木桥市集几点集合'),[])
    def test_query_scope_source_and_revision_are_bound(self):
        event=self.ingest('木桥市集十点集合。');q='木桥市集几点集合'
        snap=self.engine._snapshot('s',event);cert=self.engine._admit(q,snap,0)
        self.assertTrue(admission.validate(cert,q,snap))
        for field,value in [('scope_id','other'),('revision',2),('event_id','other'),('query_hash','x'),('snapshot_hash','x')]:
            mutated=copy.deepcopy(cert);mutated[field]=value
            self.assertFalse(admission.validate(mutated,q,snap))
        mutated=copy.deepcopy(cert);mutated['supporting_spans'][0]['start']=-1
        self.assertFalse(admission.validate(mutated,q,snap))
    def test_delete_during_admission_abstains(self):
        self.ingest('木桥市集十点集合。');original=self.engine._admit
        def race(q,s,score):
            cert=original(q,s,score);self.engine.delete_source('s',1);return cert
        self.engine._admit=race
        self.assertEqual(self.engine.build_context('s','木桥市集几点集合')['items'],[])
    def test_revision_during_admission_then_next_query(self):
        self.ingest('木桥市集十点集合。');original=self.engine._admit
        def race(q,s,score):
            cert=original(q,s,score);self.engine.revise_source('s',1,source(2,'木桥市集十一点集合。'));return cert
        self.engine._admit=race
        self.assertEqual(self.engine.build_context('s','木桥市集几点集合')['items'],[])
        self.engine._admit=original
        self.assertIn('十一点',self.engine.build_context('s','木桥市集几点集合')['items'][0]['evidence'])
    def test_delete_during_budget_counting_abstains(self):
        self.ingest('木桥市集十点集合。');engine=self.engine
        class Counter:
            encoding_name='test'
            def count(self,text):
                engine.delete_source('s',1)
                return len(text)
        p=engine.build_context('s','木桥市集',budget_tokens=1000,token_counter=Counter())
        self.assertEqual(p['items'],[]);self.assertEqual(p['rendered'],'')
    def test_provider_error_abstains(self):
        self.ingest('木桥市集十点集合。')
        def fail(*args):raise RuntimeError('simulated')
        self.engine._admit=fail
        self.assertEqual(self.engine.retrieve('s','木桥市集'),[])
    def test_nonpositive_limit_never_returns_items(self):
        self.ingest('木桥市集十点集合。')
        for limit in [-1,0]:self.assertEqual(self.engine.retrieve('s','木桥市集',limit),[])

    def test_delete_during_final_render_abstains(self):
        self.ingest('木桥市集十点集合。');engine=self.engine
        class Counter:
            def count(self,text):
                engine.delete_source('s',1)
                return len(text)
        packet=engine.build_rendered_context('s','木桥市集',1000,Counter())
        self.assertEqual(packet['items'],[]);self.assertEqual(packet['rendered'],'')
    def test_raw_same_connection_change_invalidates_certificate_cache(self):
        event=self.ingest('木桥市集十点集合。')
        self.assertTrue(self.engine.build_context('s','木桥市集几点集合')['items'])
        with self.store._write():
            self.store.db.execute('UPDATE event_evidence SET quote=? WHERE event_id=?',('木桥市集在西入口。',event))
        self.assertEqual(self.engine.build_context('s','木桥市集几点集合')['items'],[])

    def test_encoder_failure_abstains(self):
        self.ingest('木桥市集十点集合。')
        class BrokenEncoder:
            def encode(self,texts):raise ValueError('input too long')
        e=MemoryEngine(self.store,encoder=BrokenEncoder())
        trace=e.retrieve_with_audit('s','木桥市集')
        self.assertEqual(trace['items'],[])
        self.assertEqual(trace['abstained_reason'],'candidate_retrieval_error')

    def test_transient_encoder_error_is_not_cached_as_no_match(self):
        self.ingest('木桥市集十点集合。')
        class RetryEncoder:
            calls=0
            def encode(self,texts):
                self.calls+=1
                if self.calls==1:raise TimeoutError('transient')
                return [[1.0,0.0] for _ in texts]
        e=MemoryEngine(self.store,encoder=RetryEncoder())
        first=e.build_context('s','木桥市集')
        self.assertFalse(first['items']);self.assertTrue(first['degraded'])
        self.assertTrue(e.build_context('s','木桥市集')['items'])
    def test_transient_admission_error_is_not_cached_as_no_match(self):
        self.ingest('木桥市集十点集合。');original=self.engine._admit
        def fail(*args):raise TimeoutError('transient')
        self.engine._admit=fail
        self.assertFalse(self.engine.build_context('s','木桥市集')['items'])
        self.engine._admit=original
        self.assertTrue(self.engine.build_context('s','木桥市集')['items'])
