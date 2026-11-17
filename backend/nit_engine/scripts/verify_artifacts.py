#!/usr/bin/env python3
"""Cross-check deterministic artifacts; no claims about pedagogical accuracy."""
import json,argparse,hashlib
from pathlib import Path

def lines(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()] if path.exists() else []

def verify(out,source=None):
    out=Path(out);issues=[]
    def obj(name):
        try:return json.loads((out/name).read_text(encoding='utf-8'))
        except (FileNotFoundError,ValueError) as e:issues.append(f'{name}: {e}');return {}
    doc=obj('document.json');manifest=obj('manifest.json');coverage=obj('coverage_report.json') if (out/'coverage_report.json').exists() else {}
    source_sha=doc.get('source',{}).get('sha256')
    n=len(doc.get('pages',[]))
    if not source_sha:issues.append('source_sha256_missing')
    if n!=manifest.get('pages'):issues.append('manifest_page_count_mismatch')
    if source_sha!=manifest.get('sha256'):issues.append('manifest_sha_mismatch')
    if source:
        h=hashlib.sha256(Path(source).read_bytes()).hexdigest()
        if h!=source_sha:issues.append('source_file_hash_mismatch')
    pages={p['page']:p for p in doc.get('pages',[])}
    if doc.get('schema_version') in ('nit_lesson_v1_6_1','nit_lesson_v1_6_2'):
        from canonical_ir import verify as verify_ir
        ir=obj('canonical_ir.json')
        issues.extend(verify_ir(ir,doc))
    if sorted(pages)!=list(range(1,n+1)):issues.append('non_contiguous_page_ids')
    for name in ('retrieval_chunks.jsonl','section_chunks.jsonl','semantic_jobs.jsonl','ocr_jobs.jsonl'):
        for item in lines(out/name):
            if item.get('source_sha256') and item['source_sha256']!=source_sha:issues.append(name+': sha mismatch')
            for pg in ([item['page']] if 'page' in item else item.get('pages',[])):
                if pg not in pages:issues.append(f'{name}: invalid page {pg}')
            if item.get('eligible_for_coaching'):
                for span in item.get('source_spans',[]):
                    page=pages.get(span.get('page'),{});block=next((b for b in page.get('blocks',[]) if b.get('id')==span.get('block_id')),None)
                    from evidence_gate import is_block_candidate
                    if not block or not is_block_candidate(page,block):issues.append(f'{name}: unsafe eligible source {span}')
    if doc.get('source',{}).get('type')=='pdf' and doc.get('schema_version') in ('nit_lesson_v1_6','nit_lesson_v1_6_1','nit_lesson_v1_6_2'):
        arb=lines(out/'parser_arbitration.jsonl')
        if len(arb)!=n:issues.append('parser_arbitration_count_mismatch')
        for item in arb:
            page=pages.get(item.get('page'))
            if page is None:issues.append('arbitration_unknown_page');continue
            selected=item.get('selected_parser')
            if selected not in ('pypdf','pymupdf'):issues.append('arbitration_unknown_parser')
            if page.get('parser_selection',{}).get('selected_parser')!=selected:
                issues.append(f'arbitration_selection_mismatch:{item.get("page")}')
            if page.get('parser_selection',{}).get('coaching_hold'):
                if any(b.get('quality',{}).get('eligible_for_coaching') for b in page.get('blocks',[])):
                    issues.append(f'arbitration_hold_bypassed:{item.get("page")}')
    coaching=obj('coaching_index.json') if (out/'coaching_index.json').exists() else {}
    for u in coaching.get('units',[]):
        pg=pages.get(u.get('page'),{})
        for evidence in u.get('native_evidence',[]):
            block=next((b for b in pg.get('blocks',[]) if b.get('id')==evidence.get('block_id')),None)
            from evidence_gate import is_block_candidate
            if not block or not is_block_candidate(pg,block):issues.append(f'unsafe_coaching_evidence page={u.get("page")} block={evidence.get("block_id")}')
    if coverage and coverage.get('source_sha256')!=source_sha:issues.append('coverage_sha_mismatch')
    if coverage:
        parser_report=obj('parser_audit_report.json') if doc.get('source',{}).get('type')=='pdf' else {}
        if parser_report and (coverage.get('parser_review_pages') != parser_report.get('review_required_count') or
             coverage.get('parser_review_required_pages') != parser_report.get('review_required_pages')):
            issues.append('parser_review_count_inconsistent')
        if coaching:
            for field in ('candidate_evidence_pages','authorized_evidence_pages','index_ready','student_coaching_ready'):
                if field in coaching and coverage.get(field)!=coaching.get(field):issues.append('coaching_coverage_'+field+'_mismatch')
        if coverage.get('extraction_mode')=='pypdf_limited' and (coverage.get('visual_unknown_pages')!=n or coverage.get('student_coaching_ready')):
            issues.append('limited_runtime_misreported')
    result={'schema_version':'nit_artifact_audit_v1_6','passed':not issues,'source_sha256':source_sha,'page_count':n,'issue_count':len(issues),'issues':issues[:300]}
    (out/'artifact_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--source');a=p.parse_args()
    r=verify(a.out,a.source);print(json.dumps(r,ensure_ascii=False,indent=2));raise SystemExit(0 if r['passed'] else 2)
