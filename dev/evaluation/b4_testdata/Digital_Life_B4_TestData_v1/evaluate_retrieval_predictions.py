#!/usr/bin/env python3
"""Evaluate candidate-source retrieval on synthetic B4 queries.

Input predictions JSONL:
{"query_id":"Q0001","candidate_source_ids":["S01-R...","S01-R..."],"decision":"ATTACH_EXISTING"}
A matching prior source from the same gold event counts as a correct candidate.
Only input files (messages_*, retrieval_queries_input) may be fed to tested model.
"""
import argparse, json
from pathlib import Path
BASE=Path(__file__).resolve().parent

def rows(name):
    return [json.loads(z) for z in (BASE/name).read_text(encoding='utf-8').splitlines() if z.strip()]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pred',required=True,type=Path)
    args=parser.parse_args()
    qs={q['query_id']:q for q in rows('retrieval_query_gold.jsonl')}
    gold={x['source_id']:x for x in rows('message_gold.jsonl')}
    msgs={x['source_id']:x for x in rows('messages_interleaved.jsonl')}
    pred={}
    for line in args.pred.read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        obj=json.loads(line)
        qid=obj['query_id']
        assert qid in qs and qid not in pred
        assert isinstance(obj.get('candidate_source_ids'),list)
        assert obj.get('decision') in ('ATTACH_EXISTING','ABSTAIN','CREATE_NEW','UNRESOLVED')
        pred[qid]=obj
    hits={1:0,5:0,10:0}
    total=0
    unknown_correct=unknown_total=0
    contaminated=[]
    future=[]
    for qid,q in qs.items():
        o=pred.get(qid,{'candidate_source_ids':[],'decision':'UNRESOLVED'})
        candidates=o['candidate_source_ids']
        for sid in candidates:
            assert sid in msgs, f'unknown candidate source_id: {sid}'
            if msgs[sid]['scope_id']!=q['scope_id']:
                contaminated.append({'query_id':qid,'candidate_source_id':sid})
            if msgs[sid]['scope_id']==q['scope_id'] and msgs[sid]['stream_seq']>=msgs[q['source_id']]['stream_seq']:
                future.append({'query_id':qid,'candidate_source_id':sid})
        if q['gold_event_id'] is None:
            unknown_total+=1
            if o['decision']=='ABSTAIN':unknown_correct+=1
        else:
            total+=1
            for k in hits:
                if any(gold[sid]['gold_event_id']==q['gold_event_id'] and
                       msgs[sid]['scope_id']==q['scope_id'] and
                       msgs[sid]['stream_seq']<msgs[q['source_id']]['stream_seq']
                       for sid in candidates[:k]):hits[k]+=1
    print(json.dumps({'predicted_queries':len(pred),'total_queries':len(qs),
       'positive_queries':total,
       'recall_at_k':{str(k):round(v/total,5) for k,v in hits.items()},
       'unknown_correct_abstention':unknown_correct,'unknown_total':unknown_total,
       'cross_scope_candidate_count':len(contaminated),
       'future_source_leakage_count':len(future),
       'violations_examples':(contaminated+future)[:5]},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
