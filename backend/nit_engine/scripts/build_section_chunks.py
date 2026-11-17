#!/usr/bin/env python3
"""Section neighborhood retrieval view. Source pages and block IDs always retained."""
import json,argparse
from pathlib import Path
from ingest import write_jsonl

def build(out,max_chars=4500):
    out=Path(out)
    doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    jobs=[json.loads(l) for l in (out/'semantic_jobs.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    pages={p['page']:p for p in doc['pages']};chunks=[]
    for job in jobs:
        records=[]
        for pg in job['pages']:
            for b in pages[pg]['blocks']:
                from content_roles import BLOCKED_ROLES
                if pages[pg].get('content_role') in BLOCKED_ROLES or b.get('content_role') in BLOCKED_ROLES:continue
                t=b.get('text') or ' / '.join(' | '.join(r) for r in b.get('rows',[]))
                if t:records.append({'page':pg,'block_id':b['id'],'text':t,'origin':b.get('origin','native'),'eligible':b.get('quality',{}).get('eligible_for_coaching',False)})
        current=[];size=0;n=0
        def emit():
            nonlocal n
            if not current:return
            n+=1
            chunks.append({'chunk_id':f'{job["section_id"]}_n{n:03d}', 'section_id':job['section_id'],
                           'source_sha256':doc['source']['sha256'],
                           'source_spans':[{'page':r['page'],'block_id':r['block_id']} for r in current],
                           'text':'\n'.join(f'[p{r["page"]}:{r["block_id"]}] {r["text"]}' for r in current),
                           'source':'mixed' if any(r['origin']=='ocr' for r in current) else 'native',
                           'eligible_for_coaching':all(r['eligible'] for r in current), 'teacher_notes_included':False})
        for r in records:
            if current and size+len(r['text'])>max_chars:emit();current=[];size=0
            current.append(r);size+=len(r['text'])
        emit()
    write_jsonl(out/'section_chunks.jsonl',chunks)
    return len(chunks)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--max-chars',type=int,default=4500)
    a=p.parse_args();print('section_chunks',build(a.out,a.max_chars))
