#!/usr/bin/env python3
"""Execution-evidence-based completeness report. Never claim a model run without artifacts."""
import argparse,json
from pathlib import Path

def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

def report(out):
    out=Path(out)
    doc=read_json(out/'document.json')
    if not doc:raise FileNotFoundError('document.json missing; ingest must run first')
    manifest=read_json(out/'manifest.json') or {}
    sha=doc['source']['sha256'];pages=doc['pages'];n=len(pages)
    text_pages=sum(bool(' '.join(b.get('text','') for b in p['blocks']).strip()) for p in pages)
    native_pages=sum(any(b.get('origin') in ('native','native_fallback') for b in p['blocks']) for p in pages)
    ocr_completed=sum(p.get('ocr_state')=='complete' for p in pages)
    ocr_unverified=sum(any(b.get('origin')=='ocr' for b in p['blocks']) for p in pages)
    visual_pending=sum(p.get('routing',{}).get('vision_state') in ('queued','deferred','deferred_budget') for p in pages)
    jobs={}
    for kind in ('semantic','vision'):
        queue=out/f'{kind}_jobs.jsonl'
        planned=sum(bool(x.strip()) for x in queue.read_text(encoding='utf-8').splitlines()) if queue.exists() else 0
        folder=out/f'{kind}_results'
        completed=0
        for f in folder.glob('*.json') if folder.exists() else []:
            try:
                r=read_json(f)
                if r.get('source_sha256')==sha and r.get('status')=='unverified_ai_interpretation':completed+=1
            except (ValueError,OSError):pass
        jobs[kind]={'planned':planned,'completed_unverified':completed,'pending':max(planned-completed,0),
                    'execution_report':read_json(out/f'{kind}_execution_report.json')}
    quality_quarantine=sum(p.get('quality_state') in ('QUARANTINED','PARSER_REVIEW_REQUIRED') for p in pages)
    # Single source of truth: use the exact same parser review set as Parser Audit.
    parser_audit=read_json(out/'parser_audit_report.json')
    if parser_audit and parser_audit.get('source_sha256')==sha:
        parser_review_ids=parser_audit.get('review_required_pages',[])
    else:
        rows=(out/'parser_arbitration.jsonl')
        parser_review_ids=[r['page'] for r in (json.loads(x) for x in rows.read_text(encoding='utf-8').splitlines() if x.strip()) if r.get('requires_review')] if rows.exists() else []
    parser_review=len(parser_review_ids)
    visual_unknown=sum(p.get('routing',{}).get('vision_state')=='unknown_due_to_runtime' for p in pages)
    coaching_pages=sum(any(b.get('quality',{}).get('eligible_for_coaching',False) for b in p['blocks']) for p in pages)
    candidate_pages=sum(any((b.get('text') or b.get('rows')) for b in p['blocks']) for p in pages)
    limited=bool(visual_unknown)
    visual_state='unknown' if limited else ('pending_or_deferred' if visual_pending else 'inspected_no_pending')
    index_ready=(out/'retrieval_chunks.jsonl').exists()
    if not limited and visual_pending==0:
        visual_state='inspected_no_pending'  # NOT a claim that all images were understood
    faculty_verified=0
    try:
        import os
        from verified_coaching import verify
        secret=os.environ.get('NIT_REVIEW_SIGNING_KEY','')
        if len(secret)>=32:faculty_verified=len(set(e['page'] for e in verify(out,secret)))
    except (PermissionError,ValueError,KeyError,OSError):pass
    actual={'schema_version':'nit_coverage_v1_6_2','source_sha256':sha,'source_pages':n,
            'parser_engine':manifest.get('runtime_engine','unverified'),
            'native_executed':bool(manifest.get('verified_executed') and manifest.get('sha256')==sha),
            'pages_with_any_text':text_pages,'pages_with_native_text':native_pages,
            'ocr_completed_unverified':ocr_completed,'ocr_unverified_pages':ocr_unverified,
            'vision_pending_or_deferred':visual_pending,'ai_jobs':jobs,
            'parser_review_pages':parser_review,'parser_review_required_pages':parser_review_ids,'parser_arbitration':manifest.get('parser_arbitration',{}),
            'quality_quarantined_pages':quality_quarantine,'visual_unknown_pages':visual_unknown,
            'visual_inspection_status':visual_state,'extraction_mode':('pypdf_limited' if limited else manifest.get('runtime_engine','unverified')),
            'index_ready':index_ready,'candidate_evidence_pages':candidate_pages,
            'quality_pass_candidate_pages':coaching_pages,'authorized_evidence_pages':faculty_verified,
            'student_coaching_ready':bool(faculty_verified and not limited),
            'candidate_coaching_evidence_pages':coaching_pages,'faculty_verified_pages':faculty_verified, 'faculty_verification_note':'Only signed and currently valid reviewed evidence counted',
            'complete_understanding':False,'warnings':['Extracted text presence is not accuracy',
                'AI result files are unverified until approved',
                'Visual-only pages not analyzed cannot support image-specific coaching']}
    (out/'coverage_report.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf-8')
    return actual
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    print(json.dumps(report(a.out),ensure_ascii=False,indent=2))
