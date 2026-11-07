#!/usr/bin/env python3
"""Evaluate end-to-end event assignments, including pairwise F1 per scope.

Prediction JSONL: {"source_id":"S01-M01-1", "predicted_event_id":"evt_new_001"}
Event IDs in predictions can be arbitrary, but must not be reused across scopes.
Missing or null event IDs indicate ABSTAIN/UNRESOLVED.
Unknown (ambiguous) sources are counted as errors if assigned an event.
No truth IDs are needed in predictions.
"""
import argparse, json
from collections import defaultdict
from itertools import combinations
from pathlib import Path
BASE=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pred',required=True,type=Path)
    args=parser.parse_args()
    truth={x['source_id']:x for x in (json.loads(z) for z in (BASE/'message_gold.jsonl').read_text(encoding='utf-8').splitlines() if z.strip())}
    predictions={}
    for line in args.pred.read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        p=json.loads(line)
        sid=p['source_id']
        assert sid in truth and sid not in predictions
        predictions[sid]=p.get('predicted_event_id')
    by_scope=defaultdict(list)
    for sid,r in truth.items():by_scope[r['scope_id']].append(sid)
    tp=fp=fn=tn=0
    assigned=known=0
    unsupported=0
    cross_scope_violations=[]
    owner={}
    for scope,sources in by_scope.items():
        for sid in sources:
            gold=truth[sid]['gold_event_id']
            pred=predictions.get(sid)
            if gold is not None:
                known+=1
                if pred is not None:assigned+=1
            elif pred is not None:
                unsupported+=1
            if pred is not None:
                if pred in owner and owner[pred]!=scope:
                    cross_scope_violations.append((sid,pred))
                owner[pred]=scope
        valid=[s for s in sources if truth[s]['gold_event_id'] is not None]
        for a,b in combinations(valid,2):
            same_gold=truth[a]['gold_event_id']==truth[b]['gold_event_id']
            pa,pb=predictions.get(a),predictions.get(b)
            same_pred=pa is not None and pa==pb
            if same_gold and same_pred:tp+=1
            elif same_gold and not same_pred:fn+=1
            elif not same_gold and same_pred:fp+=1
            else:tn+=1
    pr=tp/(tp+fp) if tp+fp else 0.0
    re=tp/(tp+fn) if tp+fn else 0.0
    f1=2*pr*re/(pr+re) if pr+re else 0.0
    print(json.dumps({
        'pairwise_precision':round(pr,5),'pairwise_recall':round(re,5),'pairwise_f1':round(f1,5),
        'tp':tp,'fp':fp,'fn':fn,'tn':tn,
        'false_merge_per_gold_negative_pair':round(fp/(fp+tn),5) if fp+tn else None,
        'source_coverage':round(assigned/known,5),
        'unresolved_known_count':known-assigned,
        'unsupported_attach_to_ambiguous_count':unsupported,
        'cross_scope_reused_predicted_event_ids':len(cross_scope_violations),
    },ensure_ascii=False,indent=2))

if __name__=='__main__':main()
