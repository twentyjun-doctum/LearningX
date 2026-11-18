"""Single format-independent evidence gate; unknown means NOT eligible.

Native structural extraction -> possible candidate, NOT a faculty verification.
All student retrieval must still pass the signed faculty review gate.
"""
from content_roles import BLOCKED_ROLES, canonical_role

DISALLOWED_TYPES={'chart','image','drawing','placeholder','teacher_note','diagram'}

def table_valid(block):
    rows=block.get('rows',[])
    if len(rows)<2 or max((len(r) for r in rows),default=0)<2:return False
    return sum(bool(str(c).strip()) for r in rows for c in r)>=3

def is_block_candidate(page,block,allow_legacy_missing=False):
    pg_role=page.get('content_role')
    if (pg_role in BLOCKED_ROLES or
            (canonical_role(pg_role)!='lesson_content' and not (allow_legacy_missing and pg_role is None))
            or page.get('hidden_slide')):return False
    if page.get('visual_stats',{}).get('inspection_unavailable'):return False
    if page.get('routing',{}).get('vision_state')=='unknown_due_to_runtime':return False
    bl_role=block.get('content_role')
    if bl_role in BLOCKED_ROLES or (canonical_role(bl_role)!='lesson_content' and not (allow_legacy_missing and bl_role is None)):return False
    if block.get('requires_visual_confirmation'):return False
    if page.get('parser_selection',{}).get('coaching_hold'):return False
    if block.get('verification') in ('unverified_ocr','unverified_ai_interpretation','unverified_chart_data'):return False
    if block.get('origin') not in ('native','native_fallback'):return False
    typ=block.get('type','text')
    if typ in DISALLOWED_TYPES:return False
    if typ=='table' and not table_valid(block):return False
    if typ not in ('text','table'):return False
    return block.get('quality',{}).get('status')=='NATIVE_UNREVIEWED'

def enforce(doc):
    count=0
    allow_legacy=not doc.get('schema_version')  # only pre-schema historic test/doc compatibility
    for p in doc.get('pages',[]):
        for b in p.get('blocks',[]):
            q=b.setdefault('quality',{})
            eligible=is_block_candidate(p,b,allow_legacy_missing=allow_legacy)
            q['eligible_for_coaching']=eligible
            q['validation_policy']='typed_evidence_v1_6_1_candidate_only'
            q['student_usable']=False   # faculty signature required; not inferred from text
            if not eligible:
                q.setdefault('reasons',[])
                reason='content_role_or_source_gate' if p.get('content_role') in BLOCKED_ROLES else 'typed_or_quality_gate'
                if reason not in q['reasons']:q['reasons'].append(reason)
            count+=int(eligible)
    return count
