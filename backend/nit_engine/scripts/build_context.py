#!/usr/bin/env python3
"""Collect unverified AI results as separate overlays. Never alter native source blocks."""
import argparse,json
from pathlib import Path

def load(p):return json.loads(p.read_text(encoding='utf-8'))

def build(out):
    out=Path(out);source=load(out/'document.json')
    overlays=[]
    for kind in ('semantic','vision'):
        for f in sorted((out/(kind+'_results')).glob('*.json')) if (out/(kind+'_results')).exists() else []:
            rec=load(f)
            if rec.get('status')!='unverified_ai_interpretation' or rec.get('source_sha256')!=source['source']['sha256']:continue
            overlays.append({'kind':kind,'job':rec['job'],'output':rec['output'],'model':rec['model'],
                             'verified_by_teacher':False})
    # Overlays are never silently promoted into student search or coaching.
    result={'schema_version':'nit_question_context_v1','source_sha256':source['source']['sha256'],
            'document_pages':len(source['pages']),'ai_overlays':overlays,
            'policy':{'source_only_answers':True,'student_writes_final_question':True,
                      'teacher_approval_required_for_generated_cases':True,
                      'unknown_evidence_policy':'do_not_invent'}}
    (out/'question_context.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return len(overlays)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args();print('overlays',build(a.out))
