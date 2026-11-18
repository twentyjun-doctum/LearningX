#!/usr/bin/env python3
"""Routing metrics require independently verified reference labels; no denominator gaming."""
import argparse,json,os
from review_auth import is_signed
from pathlib import Path

def evaluate(out,minimum_verified=20):
    out=Path(out); dataset=json.loads((out/'evaluation_review_set.json').read_text(encoding='utf-8'))
    doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    if dataset['source_sha256']!=doc['source']['sha256']:raise ValueError('Source mismatch')
    bypage={p['page']:p for p in doc['pages']}
    verified=[r for r in dataset['records'] if r['review_status']=='faculty_verified' and
              isinstance(r['visual_required'],bool) and r.get('reviewer_id')]
    tp=fp=tn=fn=0
    for r in verified:
        state=bypage[r['page']].get('routing',{}).get('vision_state')
        # Unknown due to runtime is NOT a negative prediction; omit from routing evaluation.
        if state in ('unknown_due_to_runtime',None):continue
        predicted=state in ('queued','deferred','complete')
        actual=r['visual_required']
        if predicted and actual:tp+=1
        elif predicted and not actual:fp+=1
        elif not predicted and actual:fn+=1
        else:tn+=1
    used=tp+fp+tn+fn
    outmetrics={'schema_version':'nit_eval_metrics_v1_5','verified_labels_total':len(verified),'routable_verified_labels':used,
      'min_verified_for_release':minimum_verified,'confusion_matrix':{'tp':tp,'fp':fp,'tn':tn,'fn':fn},
      'precision':round(tp/(tp+fp),4) if tp+fp else None,
      'recall':round(tp/(tp+fn),4) if tp+fn else None,
      'release_evaluation_ready':used>=minimum_verified and tp+fn>0,
      'status':'measured_on_signed_verified_labels' if used else 'not_measured_no_signed_verified_labels',
      'caution':'Not evidence of overall accuracy unless labeled sample is representative'}
    (out/'routing_metrics.json').write_text(json.dumps(outmetrics,ensure_ascii=False,indent=2),encoding='utf-8')
    return outmetrics
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--minimum-verified',type=int,default=20)
    a=ap.parse_args();print(json.dumps(evaluate(a.out,a.minimum_verified),ensure_ascii=False,indent=2))
