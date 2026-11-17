#!/usr/bin/env python3
"""Reproducible evaluation, 0-5 per criterion, weighted total out of 100."""
import json, argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def evaluate(config):
    weights=list(config['weights'].values()); assert sum(weights)==100
    out=[]
    for item in config['ideas']:
        score=round(sum(a*w for a,w in zip(item['scores'],weights))/5,1)
        decision='ADOPT' if item['gate']=='pass' and score>=config['minimum_score'] else ('REJECT' if item['gate']=='fail' else 'DEFER')
        out.append({**item,'score':score,'decision':decision})
    return sorted(out,key=lambda x:-x['score'])
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out');opts=a.parse_args()
    cfg=json.loads((ROOT/'config/selection.json').read_text())
    rows=evaluate(cfg)
    print('ID | SCORE | RESULT | IDEA')
    for r in rows:print(f"{r['id']} | {r['score']} | {r['decision']} | {r['name']}")
    if opts.out:Path(opts.out).write_text(json.dumps({'weights':cfg['weights'],'threshold':cfg['minimum_score'],'results':rows},ensure_ascii=False,indent=2))
