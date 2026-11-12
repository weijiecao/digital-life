"""Development counterexamples, not an independent blind holdout.

Run against --repo pointing at either RC3 or the patched dev directory.
Every row retains returned IDs, references, and admission decisions.
"""
import argparse,json,hashlib,sys,tempfile
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',default=str(Path(__file__).resolve().parents[1]));p.add_argument('--model-dir');p.add_argument('--out',required=True)
    a=p.parse_args();sys.path.insert(0,a.repo)
    from evaluation.quality_acceptance import _encoder
    from memory_engine.engine import MemoryEngine
    from v1_5_consolidator.store import V15Store
    from v1_5_consolidator.contracts import SourceEnvelope
    encoder=_encoder(a.model_dir)
    def src(seq,text):return SourceEnvelope('s',seq,hashlib.sha256(text.encode()).hexdigest(),'t',text)
    groups=[
        ('missing',[('market','木桥市集在西入口集合。',None)], [
            ('market','木桥市集在哪里集合'),(None,'木桥市集几点集合'),(None,'木桥市集门票多少钱'),(None,'木桥市集要多久'),(None,'为什么选木桥市集'),(None,'木桥市集的付款凭证编号是什么'),(None,'我的牙科复查怎么安排')]),
        ('subject',[
            ('me','我周六去银行，上午十点。',{'subject':'user','reality_status':'real'}),
            ('mother','妈妈周六去银行，上午十一点。',{'subject':'family','reality_status':'real'}),
            ('friend','朋友周六去银行，下午两点。',{'subject':'friend','reality_status':'real'}),
            ('sunday','我周日去银行，下午三点。',{'subject':'user','reality_status':'real'})], [
                ('me','我周六去银行几点'),('mother','妈妈周六去银行几点'),('friend','朋友周六去银行几点'),('sunday','我周日去银行几点'),(None,'爸爸周六去银行几点'),(None,'我周五去银行几点')]),
        ('unknown',[('unknown','周六去银行，上午十点。',None)],[(None,'我周六去银行几点'),('unknown','银行周六几点去')]),
        ('attributes',[('market','木桥市集周六十点在西入口集合。门票50元，活动两小时。因为下雨而取消。',None)], [
            ('market','木桥市集门票多少钱'),('market','木桥市集活动多久'),('market','为什么取消木桥市集'),('market','木桥市集几点集合'),(None,'木桥市集联系人号码是什么')]),
        ('story',[('story','故事里的阿岚周四去海边，南岸集合。',{'subject':'story_character','reality_status':'fictional','story_id':'a'})],[
            ('story','故事角色周四去海边哪里'),(None,'故事角色周末去海边哪里'),(None,'真实的我周四去海边哪里')]),
    ]
    rows=[]
    for group,events,queries in groups:
        with tempfile.TemporaryDirectory() as d:
            s=V15Store(Path(d)/'db')
            try:
                e=MemoryEngine(s,encoder=encoder);labels={}
                for seq,(label,text,prov) in enumerate(events,1):labels[e.ingest('s',src(seq,text),prov).event_id]=label
                for expected,q in queries:
                    audit=e.retrieve_with_audit('s',q) if hasattr(e,'retrieve_with_audit') else {'items':e.retrieve('s',q)}
                    packet=e.build_context('s',q)
                    returned=[labels[i['event_id']] for i in packet['items']]
                    rows.append({'group':group,'query':q,'expected':expected,'returned':returned,'pass':returned==([expected] if expected else []),'audit':audit})
            finally:s.close()
    positives=[r for r in rows if r['expected']];negative=[r for r in rows if not r['expected']]
    result={'kind':'development_counterexamples_not_blind','encoder':'BGE' if encoder else 'lexical','positive_hits':sum(r['expected'] in r['returned'] for r in positives),'positive_count':len(positives),'positive_extra_injections':sum(any(x!=r['expected'] for x in r['returned']) for r in positives),'negative_injections':sum(bool(r['returned']) for r in negative),'negative_count':len(negative),'pass':all(r['pass'] for r in rows),'rows':rows}
    Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rows'}));return 0 if result['pass'] else 1
if __name__=='__main__':raise SystemExit(main())
