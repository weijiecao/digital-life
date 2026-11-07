#!/usr/bin/env python3
"""Validate B4 synthetic fixture consistency; standard library only."""
import json
from collections import Counter
from pathlib import Path
BASE=Path(__file__).resolve().parent

def load(name):
    return [json.loads(x) for x in (BASE/name).read_text(encoding='utf-8').splitlines() if x.strip()]

def check():
    msgs=load('messages_interleaved.jsonl')
    cont=load('messages_contiguous.jsonl')
    msg_gold=load('message_gold.jsonl')
    events=load('gold_events.jsonl')
    pairs=load('pair_gold.jsonl')
    pairs_in=load('pair_input.jsonl')
    queries=load('retrieval_query_gold.jsonl')
    queries_in=load('retrieval_queries_input.jsonl')
    anchors=load('gold_event_anchors_DO_NOT_FEED.jsonl')
    m={r['source_id']:r for r in msgs}
    mc={r['source_id']:r for r in cont}
    mg={r['source_id']:r for r in msg_gold}
    e={r['event_id']:r for r in events}
    assert len(m)==len(msgs)==len(mc)==len(cont)==len(mg)==len(msg_gold)==340
    assert set(m)==set(mc)==set(mg)
    assert len(e)==len(events)==len(anchors)==80
    assert len(pairs)==len(pairs_in)==610
    assert len(queries)==len(queries_in)==180
    assert len({p['pair_id'] for p in pairs})==len(pairs)
    assert len({q['query_id'] for q in queries})==len(queries)
    assert 'gold_event_id' not in msgs[0]
    assert 'record_kind' not in msgs[0]
    assert 'relation' not in pairs_in[0]
    assert 'gold_event_id' not in queries_in[0]
    for mid,r in m.items():
        assert r['text']==mc[mid]['text']
        assert r['is_synthetic'] and r['scope_id']==mid[:3]
        assert 'M01' not in mid and '-AMB' not in mid, 'Source ID leaks event index'
        if mg[mid]['gold_event_id'] is not None:
            assert mid in e[mg[mid]['gold_event_id']]['source_ids']
            assert r['scope_id']==e[mg[mid]['gold_event_id']]['scope_id']
        else:
            assert mg[mid]['record_kind']=='ambiguous_unresolved'
    for scope in sorted({x['scope_id'] for x in msgs}):
        for layout in (msgs,cont):
            seq=sorted(x['stream_seq'] for x in layout if x['scope_id']==scope)
            assert seq==list(range(1,35)),(scope,seq)
    for p,pin in zip(pairs,pairs_in):
        assert p['pair_id']==pin['pair_id']
        a,b=mg[p['left_source_id']],mg[p['right_source_id']]
        ga,gb=a['gold_event_id'],b['gold_event_id']
        if p['relation']=='SAME_EVENT':
            assert ga and ga==gb and p['same_event'] is True
        elif p['relation'] in ('RELATED','DIFFERENT_EVENT'):
            assert ga and gb and ga!=gb and p['same_event'] is False
        else:
            assert p['relation']=='UNRESOLVED' and (ga is None or gb is None)
            assert p['same_event'] is None
    for q,qin in zip(queries,queries_in):
        assert q['query_id']==qin['query_id']
        sid=q['source_id']
        assert q['scope_id']==m[sid]['scope_id']
        assert q['gold_event_id']==mg[sid]['gold_event_id']
        if q['expected']=='ATTACH_EXISTING':
            assert q['gold_event_id'] and q['must_be_retrievable_from_prior_sources']
            assert all(m[x]['scope_id']==q['scope_id'] for x in q['must_be_retrievable_from_prior_sources'])
            assert all(m[x]['stream_seq']<m[sid]['stream_seq'] for x in q['must_be_retrievable_from_prior_sources'])
        else:
            assert q['expected']=='ABSTAIN' and q['gold_event_id'] is None
    print('VALID: 340 messages × 2 layouts, 80 events, 610 labeled pairs, 180 queries, 12 reliability scenarios.')
    print('Pair labels:',dict(Counter(p['relation'] for p in pairs)))
    print('Event types:',dict(Counter(ev['event_type'] for ev in events)))
    print('Model-visible input has no gold labels; source IDs are opaque.')

if __name__=='__main__':check()
