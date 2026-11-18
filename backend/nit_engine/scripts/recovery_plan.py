#!/usr/bin/env python3
"""Build prioritized remediation jobs; plugin adapters remain optional and unexecuted."""
import json,argparse
from pathlib import Path

def plan(out):
    out=Path(out);doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    jobs=[]
    for p in doc['pages']:
        quality=p.get('quality_state','UNKNOWN')
        visual_unknown=p.get('routing',{}).get('vision_state')=='unknown_due_to_runtime' or p.get('visual_stats',{}).get('inspection_unavailable',False)
        raw=' '.join(b.get('text','') for b in p['blocks'])
        if quality in ('QUARANTINED','PARSER_REVIEW_REQUIRED'):
            priority=100;route='OCR_OR_LAYOUT_REVIEW';reason='suspected_text_corruption_or_parser_disagreement'
        elif not raw.strip():priority=70;route='BLANK_VS_VISUAL_TRIAGE';reason='zero_native_text'
        elif quality=='LOW_CONTENT_REVIEW':priority=45;route='VISUAL_TRIAGE';reason='too_little_evidence'
        elif visual_unknown:priority=20;route='VISUAL_CAPABILITY_PENDING';reason='runtime_cannot_inspect_visuals'
        else:continue
        jobs.append({'page':p['page'],'priority':priority,'route':route,'reason':reason,'source_sha256':doc['source']['sha256'], 'status':'pending_not_executed'})
    jobs.sort(key=lambda x:(-x['priority'],x['page']))
    with (out/'recovery_jobs.jsonl').open('w',encoding='utf-8') as f:
        for j in jobs:f.write(json.dumps(j,ensure_ascii=False)+'\n')
    return {'count':len(jobs),'by_route':{route:sum(j['route']==route for j in jobs) for route in sorted(set(j['route'] for j in jobs))}}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out',required=True);args=a.parse_args();print(json.dumps(plan(args.out),ensure_ascii=False,indent=2))
