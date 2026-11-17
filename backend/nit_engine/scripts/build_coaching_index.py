#!/usr/bin/env python3
"""Build deterministic source-grounded scaffolds; never invent definitions or questions as source facts."""
import argparse, json, re
from pathlib import Path
from policy import authorize
STEMS={
 'apply':'이 개념을 다른 상황에 적용하면 어떤 차이가 나타날까요?',
 'analyze':'이 현상의 원인과 결과를 구분하면 무엇이 달라지나요?',
 'evaluate':'이 주장에 필요한 근거 또는 반례는 무엇인가요?',
 'create':'이 설명의 한계를 보완하기 위해 어떤 조건을 바꿔볼 수 있을까요?'
}
def build(out,policy='RESTRICTED_HINT_ONLY',approved=False):
    out=Path(out)
    data=json.loads((out/'document.json').read_text(encoding='utf-8'))
    source=data['source']; context=[]
    for page in data['pages']:
        lines=[]
        for block in page['blocks']:
            from evidence_gate import is_block_candidate
            if not is_block_candidate(page,block):continue
            t=block.get('text') or ' / '.join(' | '.join(r) for r in block.get('rows',[]))
            if t and block.get('quality',{}).get('eligible_for_coaching',False) and block.get('origin') != 'ocr' and block.get('verification') not in ('unverified_ocr','unverified_ai_interpretation'):
                lines.append({'block_id':block['id'],'text':t[:1500], 'source_type':block.get('source_type','native'), 'evidence_status':'native_extracted_unreviewed'})
        if not lines:continue
        context.append({'page':page['page'],'source_sha256':source['sha256'], 'native_evidence':lines,
                        'content_status':'NATIVE_ONLY' if page['routing']['vision_state']!='not_required' else 'FAST_INDEX',
                        'vision_state':page['routing']['vision_state'],
                        'scaffolds':{}, # v1.5: no student scaffolds until verified_coaching signature gate
                        'teacher_notes_included':False})
    limited=any(p.get('routing',{}).get('vision_state')=='unknown_due_to_runtime' for p in data['pages'])
    authorized_pages=0
    try:
        import os
        from verified_coaching import verify
        secret=os.environ.get('NIT_REVIEW_SIGNING_KEY','')
        if len(secret)>=32:authorized_pages=len({e['page'] for e in verify(out,secret)})
    except (PermissionError,ValueError,KeyError,OSError):pass
    doc={'schema_version':'nit_coaching_index_v1_6_2','policy':policy,'teacher_approved':approved,
         'source_sha256':source['sha256'],'index_ready':(out/'retrieval_chunks.jsonl').exists(),
         'extraction_mode':('pypdf_limited' if limited else 'native_full_or_adaptive'),
         'visual_inspection_status':('unknown' if limited else 'inspected_routing_only'),
         'candidate_evidence_pages':sum(any(b.get('text') or b.get('rows') for b in p['blocks']) for p in data['pages']),
         'quality_pass_candidate_pages':len(context),
         'authorized_evidence_pages':authorized_pages,'student_coaching_ready':bool(authorized_pages and not limited), 'evidence_quality_gate':'v1.5_requires_signed_faculty_review', 'faculty_evidence_verified':False, 'teacher_notes_access':'server_authorized_only',
         'units':context,'notice':'These are review candidates, not student-usable evidence. Run verified_coaching gate with server-authenticated faculty signatures.'}
    dest=out/'coaching_index.json';dest.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'units':len(context),'policy':policy,'ai_coach_enabled':False,'output':str(dest)}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out',required=True)
    a.add_argument('--policy',choices=['PROHIBITED','RESTRICTED_HINT_ONLY','PERMITTED'],default='RESTRICTED_HINT_ONLY')
    a.add_argument('--teacher-approved',action='store_true');args=a.parse_args()
    print(json.dumps(build(args.out,args.policy,args.teacher_approved),ensure_ascii=False,indent=2))
