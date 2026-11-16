"""E5.0-B: offline reviewed-snapshot progressive clue evaluation.

No online QQ/AstrBot hooks; never reads legacy memory DB. Reports omit source
verbatim and user clues. Real BGE optional; CI uses explicit FAKE embeddings.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from tempfile import TemporaryDirectory
from ..e418.engine import UnifiedMemoryEngine
from ..e419.dynamic_index import attach_dynamic_index
from ..e421.progressive import ProgressiveRecall
from ..e50b.snapshot import load_snapshot,import_snapshot,SnapshotError

class _FixtureEncoder:
    model_key='fake-e50b-fixture-3d-v1'
    def __init__(self):self.documents=0
    def _vec(self,s):
        if '书店' in s or '买书' in s or '资料' in s:return [1,0,0,0]
        if '足球' in s:return [0,1,0,0]
        if '电影' in s:return [0,0,1,0]
        return [0,0,0,1]
    def encode_documents(self,texts):
        self.documents+=len(texts)
        return [self._vec(t) for t in texts]
    def encode_queries(self,texts):return [self._vec(t) for t in texts]

def validate_cases(data,known):
    if not isinstance(data,dict) or set(data)!={'format','cases'} or data['format']!='digitallife-e50b-cases-v1':
        raise SnapshotError('invalid case schema')
    cases=data['cases']
    if not isinstance(cases,list) or not 1<=len(cases)<=100:raise SnapshotError('invalid case count')
    seen=set()
    for case in cases:
        if not isinstance(case,dict) or set(case)!={'case_id','clues','expected_external_id','old_only_detail','expected_old_source_id'}:
            raise SnapshotError('invalid case fields')
        cid=case['case_id']
        if not isinstance(cid,str) or not cid or len(cid)>80 or cid in seen:
            raise SnapshotError('invalid/duplicate case id')
        seen.add(cid)
        qs=case['clues']
        if not isinstance(qs,list) or not 1<=len(qs)<=4 or any(not isinstance(q,str) or not q or len(q)>400 for q in qs):
            raise SnapshotError('bad clues')
        eid=case['expected_external_id']
        sid=case['expected_old_source_id']
        detail=case['old_only_detail']
        if eid is not None and eid not in known:raise SnapshotError('expected event missing')
        if eid is None:
            if sid is not None or detail is not None:raise SnapshotError('negative case must omit source')
        else:
            if not isinstance(detail,str) or not detail.strip() or any(detail in q for q in qs):
                raise SnapshotError('held-out detail leaked into new clues')
            if not isinstance(sid,str) or not sid.strip():raise SnapshotError('expected old source missing')
    return cases

def run(snapshot,cases,*,encoder=None,simulate_fixture_confirmation=False):
    # No persistent DB, no logging of personal text, no system/network effects.
    encoder=encoder or _FixtureEncoder()
    scope=snapshot['scope']
    known={e['external_id'] for e in snapshot['events']}
    cases=validate_cases(cases,known)
    source_to_event={src['external_source_id']:(e['external_id'],src['content'])
        for e in snapshot['events'] for src in e['sources']}
    for c in cases:
        if c['expected_external_id'] is not None:
            record=source_to_event.get(c['expected_old_source_id'])
            if not record or record[0]!=c['expected_external_id'] or c['old_only_detail'] not in record[1]:
                raise SnapshotError('held-out source not found in reviewed old event')
    with TemporaryDirectory(prefix='e50b_eval_') as tmp:
        with UnifiedMemoryEngine(Path(tmp)/'scratch.sqlite3') as flow:
            imported_scope,mapping,sources=import_snapshot(flow,snapshot)
            assert imported_scope==scope
            idx=attach_dynamic_index(flow,encoder,min_cosine=.55,limit=3)
            rows=[]
            for c in cases:
                progressive=ProgressiveRecall(flow,idx.search)
                session='e50b:'+c['case_id']
                expected=mapping.get(c['expected_external_id'])
                per_turn=[];response=None
                for turn,q in enumerate(c['clues'],1):
                    response=progressive.turn(scope=scope,session=session,question=q)
                    p=response['packet']
                    hits=[x['event_id'] for x in p.get('associations',[])]
                    # Explicit titles are allowed through E4.18 source gate,
                    # but not counted as semantic candidate recovery.
                    per_turn.append({'turn':turn,'target_in_candidates':expected in hits if expected else False,
                                     'candidate_count':len(hits),'has_evidence_before_confirmation':bool(p['fact_evidence']),
                                     'top_cosine':idx.last_diagnostics[0]['cosine'] if idx.last_diagnostics else None,
                                     'status':p['status']})
                last=per_turn[-1]
                assert response is not None
                last_hits=response['packet'].get('associations',[])
                confirmed=False;old_source_found=False
                if simulate_fixture_confirmation and expected and len(last_hits)==1 and last_hits[0]['event_id']==expected:
                    progressive.mark_presented(scope,session,expected)
                    confirm=progressive.turn(scope=scope,session=session,question='对，就是这个。')
                    evidence=confirm['packet'].get('fact_evidence',[])
                    confirmed=True
                    old_source_found=any(r['message_id']==sources[c['expected_old_source_id']]
                        and c['old_only_detail'] in r['verbatim'] for r in evidence)
                rows.append({'case_id':c['case_id'],'expected_event_present':expected is not None,
                             'first_turn_hit':per_turn[0]['target_in_candidates'],
                             'final_turn_hit':last['target_in_candidates'],
                             'first_miss_then_recovered':not per_turn[0]['target_in_candidates'] and last['target_in_candidates'] if expected else False,
                             'final_wrong_candidate':bool(last_hits) and expected not in [r['event_id'] for r in last_hits],
                             'confirmation_simulated':confirmed,'old_source_found':old_source_found,
                             'turns':per_turn})
            positives=[x for x in rows if x['expected_event_present']]
            negatives=[x for x in rows if not x['expected_event_present']]
            return {'stage':'E5.0-B','encoder_type':'synthetic' if isinstance(encoder,_FixtureEncoder) else 'provided',
                'threshold_unchanged':.55,'events':len(mapping),'cases':len(rows),
                'positive_cases':len(positives),'negative_cases':len(negatives),
                'final_recall':sum(x['final_turn_hit'] for x in positives),
                'first_miss_then_recovered':sum(x['first_miss_then_recovered'] for x in positives),
                'negative_false_candidate_cases':sum(x['final_wrong_candidate'] for x in negatives),
                'old_source_recovered_after_SIMULATED_confirmation':sum(x['old_source_found'] for x in rows),
                'raw_chat_text_printed':False,'astrbot_connected':False,
                'review_independently_verified':False,'case_results':rows}

def main():
    p=argparse.ArgumentParser(description='E5.0-B offline approved snapshot eval; NO QQ access')
    p.add_argument('--snapshot',required=True)
    p.add_argument('--cases',required=True)
    p.add_argument('--authorized',action='store_true',help='Explicit consent and manual review confirmed by operator')
    p.add_argument('--model-dir',help='Optional local BGE model path; default synthetic encoder')
    p.add_argument('--threads',type=int,default=4)
    p.add_argument('--fixture-confirmation',action='store_true',help='Simulated confirmation ONLY for synthetic fixture')
    args=p.parse_args()
    snap=load_snapshot(args.snapshot,authorized=args.authorized)
    if Path(args.cases).stat().st_size>1024*1024:raise SnapshotError('case file too large')
    cases=json.loads(Path(args.cases).read_text(encoding='utf-8'))
    if args.fixture_confirmation and not (snap['scope'].startswith('fictional:') and 'fixture' in Path(args.snapshot).name):
        raise SnapshotError('simulated confirmation restricted to fictional fixture')
    if not args.model_dir and not snap['scope'].startswith('fictional:'):
        raise SnapshotError('real snapshots require --model-dir; synthetic embeddings only for fiction')
    encoder=None
    if args.model_dir:
        from e32.cpu_model import CPUSentenceEmbedder
        encoder=CPUSentenceEmbedder(args.model_dir,cpu_threads=args.threads,batch_size=8)
    result=run(snap,cases,encoder=encoder,simulate_fixture_confirmation=args.fixture_confirmation)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
