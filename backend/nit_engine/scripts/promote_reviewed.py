#!/usr/bin/env python3
"""Explicit review gate for model overlays. No automatic promotion.

CLI requires a review manifest; the deployment must separately authenticate reviewer.
"""
import argparse,hashlib,json
from pathlib import Path
from ingest import write_jsonl

def filehash(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def promote(out,review_path):
    out=Path(out);review_path=Path(review_path)
    source=json.loads((out/'document.json').read_text(encoding='utf-8'))
    review=json.loads(review_path.read_text(encoding='utf-8'))
    sha=source['source']['sha256']; approved=[]; rejected=[]
    if review.get('source_sha256')!=sha:raise ValueError('Review source SHA mismatch')
    if not review.get('reviewer_id') or not isinstance(review.get('items'),list):
        raise ValueError('Reviewer and items are required')
    for item in review['items']:
        kind=item.get('kind');name=item.get('result_filename')
        if kind not in ('vision','semantic') or not isinstance(name,str) or Path(name).name!=name or not name.endswith('.json'):
            raise ValueError('Invalid review result reference')
        path=out/f'{kind}_results'/name
        if not path.is_file() or filehash(path)!=item.get('result_sha256'):
            raise ValueError(f'Review file missing or digest mismatch: {name}')
        record=json.loads(path.read_text(encoding='utf-8'))
        if record.get('source_sha256')!=sha or record.get('status')!='unverified_ai_interpretation':
            raise ValueError(f'Invalid model result: {name}')
        if item.get('decision')!='approve':
            rejected.append({'result':name,'kind':kind});continue
        job=record['job'];page_ids=[job['page']] if kind=='vision' else job.get('pages',[])
        if not page_ids or any(not isinstance(p,int) or p<1 or p>len(source['pages']) for p in page_ids):
            raise ValueError('Missing or invalid source-page evidence')
        approved.append({'kind':kind,'result':name,'pages':page_ids,'source_sha256':sha,
            'source_verified_by_reviewer':True,'reviewer_id':review['reviewer_id'],
            'review_note':item.get('review_note',''), 'content':record['output'],
            'status':'reviewed_interpretation_not_source_quote'})
    write_jsonl(out/'reviewed_overlays.jsonl',approved)
    result={'source_sha256':sha,'reviewer_id':review['reviewer_id'],
            'approved':len(approved),'rejected':len(rejected),'not_reviewed':'remains_unverified',
            'warning':'Application must authenticate reviewer and enforce ACL before serving these records'}
    (out/'review_promotion_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',required=True);ap.add_argument('--review',required=True)
    a=ap.parse_args();print(json.dumps(promote(a.out,a.review),ensure_ascii=False,indent=2))
