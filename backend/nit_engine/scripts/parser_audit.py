#!/usr/bin/env python3
"""Evidence-based parser selection report for a completed v1.6 PDF ingest."""
import argparse, json
from pathlib import Path
from collections import Counter

def audit(out):
    out=Path(out)
    doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    rows=[json.loads(x) for x in (out/'parser_arbitration.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    n=len(doc['pages'])
    if len(rows)!=n:raise ValueError(f'Incomplete parser audit: {len(rows)}/{n} pages')
    if manifest.get('sha256')!=doc['source']['sha256']:raise ValueError('Source hash mismatch')
    cnt=Counter(x['selected_parser'] for x in rows)
    review=[x['page'] for x in rows if x.get('requires_review')]
    disagreement=[x['page'] for x in rows if 'parser_disagreement_review' in x.get('reason','')]
    report={'schema_version':'nit_parser_audit_v1_6','source_sha256':doc['source']['sha256'],
            'source_pages':n,'comparison_mode':manifest.get('parser_arbitration',{}).get('comparison_mode'),
            'pages_compared':sum(x.get('comparison_performed',False) for x in rows),
            'selected':dict(cnt),'review_required_count':len(review),'review_required_pages':review,
            'disagreement_count':len(disagreement),'disagreement_pages':disagreement,
            'visual_and_ocr_accuracy_measured':False,
            'note':'Heuristic text-quality selection; no claim of ground-truth CER, native text completeness, or vision recall.'}
    (out/'parser_audit_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return report
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    print(json.dumps(audit(a.out),ensure_ascii=False,indent=2))
