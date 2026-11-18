"""Canonical representation shared by PDF/PPTX and consumed by verification.

Not a vision rendering proof: source-derived vs inherited vs unverified retained.
"""
import json
from pathlib import Path
IR_SCHEMA='nit_canonical_ir_v1_6_2'

def build(doc):
    pages=[]
    for p in doc['pages']:
        blocks=[]
        for b in p['blocks']:
            blocks.append({'id':b['id'],'type':b.get('type','text'),'role':b.get('canonical_role',b.get('content_role','unknown')), 'legacy_role':b.get('content_role','unknown'),
                           'text':b.get('text',''),'raw_text':b.get('raw_text',b.get('text','')),
                           'rows':b.get('rows'),'chart_data':b.get('chart_data'),
                           'paragraphs':b.get('paragraphs'),'bbox':b.get('bbox'),
                           'source_type':b.get('source_type'),'source_page':p['page'],
                           'object_id':b.get('object_id'),
                           'extraction_origin':b.get('origin'),
                           'requires_visual_confirmation':b.get('requires_visual_confirmation',False),
                           'quality_status':b.get('quality',{}).get('status','UNKNOWN'),
                           'candidate_for_faculty_review':bool(b.get('quality',{}).get('eligible_for_coaching',False)),
                           'student_usable':False})
        pages.append({'page':p['page'],'content_role':p.get('canonical_role',p.get('content_role','unknown')), 'legacy_role':p.get('content_role','unknown'),
                      'hidden_slide':p.get('hidden_slide',False),
                      'routing':p.get('routing',{}),'blocks':blocks})
    return {'schema_version':IR_SCHEMA,'source':doc['source'],'pages':pages,
            'lesson_boundaries':doc.get('lesson_boundaries',[]),
            'student_output_policy':'fail_closed_signed_faculty_evidence_only'}

def save(out,doc):
    path=Path(out)/'canonical_ir.json'
    path.write_text(json.dumps(build(doc),ensure_ascii=False,indent=2),encoding='utf-8')
    return path

def verify(ir,doc):
    issues=[]
    if ir.get('schema_version')!=IR_SCHEMA:issues.append('canonical_ir_schema_mismatch')
    if ir.get('source',{}).get('sha256')!=doc.get('source',{}).get('sha256'):issues.append('canonical_ir_sha_mismatch')
    if len(ir.get('pages',[]))!=len(doc.get('pages',[])):issues.append('canonical_ir_page_count_mismatch')
    for p,ref in zip(ir.get('pages',[]),doc.get('pages',[])):
        if p.get('page')!=ref.get('page') or p.get('content_role')!=ref.get('canonical_role',ref.get('content_role')):
            issues.append(f'canonical_ir_page_metadata_mismatch_{ref.get("page")}')
        if len(p.get('blocks',[]))!=len(ref.get('blocks',[])):
            issues.append(f'canonical_ir_block_count_mismatch_{ref.get("page")}')
        for a,b in zip(p.get('blocks',[]),ref.get('blocks',[])):
            if a.get('id')!=b.get('id') or (a.get('text') or '')!=(b.get('text') or ''):
                issues.append(f'canonical_ir_block_mismatch_{ref.get("page")}_{b.get("id")}')
            if a.get('candidate_for_faculty_review') and not b.get('quality',{}).get('eligible_for_coaching'):
                issues.append(f'canonical_ir_unsafe_candidate_{ref.get("page")}_{b.get("id")}')
            if a.get('student_usable'):issues.append('canonical_ir_cannot_authorize_students')
    return issues
