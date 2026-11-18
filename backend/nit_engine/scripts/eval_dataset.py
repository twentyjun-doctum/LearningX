#!/usr/bin/env python3
"""Produce stratified, unlabelled 100-page review set. Never auto-certify truth."""
import argparse,hashlib,json,random
from pathlib import Path

def build(out,n=100,seed=1505):
    out=Path(out); doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    pages=doc['pages']; sha=doc['source']['sha256']; n=min(n,len(pages))
    strata={'zero_text':[],'quality_flagged':[],'visual_uncertain':[],'native_candidate':[]}
    for p in pages:
        text=' '.join(str(b.get('text','')) for b in p['blocks']).strip()
        quality=p.get('quality_state','UNKNOWN')
        if not text:strata['zero_text'].append(p)
        if text and quality in ('QUARANTINED','LOW_CONTENT_REVIEW'):strata['quality_flagged'].append(p)
        if p.get('routing',{}).get('vision_state') in ('queued','deferred','unknown_due_to_runtime'):
            strata['visual_uncertain'].append(p)
        if text and quality=='NATIVE_UNREVIEWED':strata['native_candidate'].append(p)
    rnd=random.Random(seed)
    targets={'zero_text':25,'quality_flagged':30,'visual_uncertain':25,'native_candidate':20}
    selected=[]; seen=set()
    # Independent categories may overlap; avoid duplicates without assuming visual unknown means bad text.
    for label,pgs in strata.items():
        r=pgs[:];rnd.shuffle(r);added=0
        for p in r:
            if p['page'] in seen:continue
            if added>=targets[label] or len(selected)>=n:break
            selected.append((p,label));seen.add(p['page']);added+=1
    rest=[(p,'supplemental') for p in pages if p['page'] not in seen]
    rnd.shuffle(rest)
    selected.extend(rest[:max(0,n-len(selected))])
    records=[]
    for p,label in sorted(selected,key=lambda x:x[0]['page']):
        text=' '.join(str(b.get('text','')) for b in p['blocks'])
        records.append({'page':p['page'],'stratum':label,'source_sha256':sha,
          'native_preview':text[:160],'candidate_visual_priority':p.get('routing',{}).get('vision_priority'),
          'review_status':'unlabelled','label_signature':None,'reviewer_id':None,'visual_required':None,
          'text_quality_acceptable':None,'ocr_required':None,'full_reference_text':None,
          'reviewed_source_page_sha256':None,'review_note':''})
    dataset={'schema_version':'nit_evalset_v1_5','source_sha256':sha,'document_pages':len(pages),
      'sampling':{'seed':seed,'requested':n,'stratification':'balanced_priority_with_random_remainder'},
      'verified_label_count':0,'annotation_policy':'Only reviewer-confirmed ground truth; native PDF text is not reference',
      'records':records}
    dest=out/'evaluation_review_set.json';dest.write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'file':str(dest),'sampled':len(records),'verified_labels':0,'strata':{k:sum(r['stratum']==k for r in records) for k in [*strata,'supplemental']}}
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--size',type=int,default=100);a=ap.parse_args()
    if a.size<1:ap.error('size >=1');print(json.dumps(build(a.out,a.size),ensure_ascii=False,indent=2))
    else:print(json.dumps(build(a.out,a.size),ensure_ascii=False,indent=2))
