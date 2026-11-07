#!/usr/bin/env python3
"""Evaluate predicted relation labels for synthetic B4 pairs.

Input JSONL: {"pair_id":"P0001", "relation":"SAME_EVENT"}
Allowed relations: SAME_EVENT, RELATED, DIFFERENT_EVENT, UNRESOLVED.
Unsubmitted predictions treated as UNRESOLVED (not as correct abstentions).
False merge rate denominator = gold negatives (RELATED + DIFFERENT_EVENT).
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
BASE=Path(__file__).resolve().parent
LABELS={'SAME_EVENT','RELATED','DIFFERENT_EVENT','UNRESOLVED'}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--pred',required=True,type=Path,help='Prediction JSONL')
    args=ap.parse_args()
    truth={x['pair_id']:x for x in (json.loads(z) for z in (BASE/'pair_gold.jsonl').read_text(encoding='utf-8').splitlines() if z.strip())}
    preds={}
    for line in args.pred.read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        p=json.loads(line)
        assert p['pair_id'] in truth, f'unknown pair_id: {p["pair_id"]}'
        assert p['relation'] in LABELS
        assert p['pair_id'] not in preds, f'duplicate pair_id: {p["pair_id"]}'
        preds[p['pair_id']]=p['relation']
    tp=fp=fn=tn=0
    missing=0
    confusion=defaultdict(Counter)
    for pid,g in truth.items():
        gold=g['relation']
        pred=preds.get(pid,'UNRESOLVED')
        if pid not in preds:missing+=1
        confusion[gold][pred]+=1
        # True UNKNOWN is excluded from binary SAME_EVENT F1.
        if gold=='UNRESOLVED':continue
        if gold=='SAME_EVENT':
            if pred=='SAME_EVENT':tp+=1
            else:fn+=1
        elif pred=='SAME_EVENT':fp+=1
        else:tn+=1
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
    negative_count=fp+tn
    abstain_on_known=sum(v.get('UNRESOLVED',0) for k,v in confusion.items() if k!='UNRESOLVED')
    correct_unknown=confusion['UNRESOLVED']['UNRESOLVED']
    results={
        'file':str(args.pred),'submitted':len(preds),'total':len(truth),
        'missing_treated_as_abstain':missing,
        'same_event_precision':round(precision,5),
        'same_event_recall':round(recall,5),
        'same_event_f1':round(f1,5),
        'same_event_tp':tp,'same_event_fp':fp,'same_event_fn':fn,
        'false_merge_rate_over_gold_negatives':round(fp/negative_count,5) if negative_count else None,
        'negative_gold_count':negative_count,
        'abstain_rate_on_known_pairs':round(abstain_on_known/(len(truth)-sum(1 for v in truth.values() if v['relation']=='UNRESOLVED')),5),
        'unresolved_gold_correct_abstentions':correct_unknown,
        'unresolved_gold_total':sum(1 for v in truth.values() if v['relation']=='UNRESOLVED'),
        'confusion_matrix':{k:dict(v) for k,v in sorted(confusion.items())},
    }
    print(json.dumps(results,indent=2,ensure_ascii=False))

if __name__=='__main__':main()
