"""Local staging CLI. Does not connect to AstrBot or call a model."""
import argparse,json
from pathlib import Path
from . import Origin,ImportJournal

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True);p.add_argument('--journal',required=True)
    for field in ('platform','bot','user','persona','conversation'):p.add_argument('--'+field,required=True)
    a=p.parse_args();origin=Origin(**{k:getattr(a,k) for k in ('platform','bot','user','persona','conversation')})
    j=ImportJournal(a.journal)
    try:
        payload=json.loads(Path(a.input).read_text(encoding='utf-8-sig'))
        print(json.dumps(j.stage(payload,origin),ensure_ascii=False))
    finally:j.close()
if __name__=='__main__':main()
