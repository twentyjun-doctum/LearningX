#!/usr/bin/env python3
"""Render reviewer-selected pages. Never infer or auto-fill reference labels."""
import argparse,json
from pathlib import Path
def render(pdf,out,dpi=85,max_pages=100):
 import fitz
 out=Path(out);data=json.loads((out/'evaluation_review_set.json').read_text(encoding='utf-8'))
 from ingest import digest
 if data['source_sha256']!=digest(Path(pdf)):raise ValueError('Source mismatch')
 folder=out/'review_images';folder.mkdir(exist_ok=True)
 rendered=[]
 with fitz.open(pdf) as doc:
  for r in data['records'][:max_pages]:
   n=r['page'];p=doc[n-1];target=folder/f'p{n:04d}.png'
   if not target.exists():target.write_bytes(p.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False).tobytes('png'))
   rendered.append(str(target))
 return {'rendered':len(rendered),'directory':str(folder),'label_status':'unlabelled'}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('pdf');ap.add_argument('--out',required=True);ap.add_argument('--dpi',type=int,default=85);ap.add_argument('--max-pages',type=int,default=100);args=ap.parse_args()
 print(json.dumps(render(args.pdf,args.out,args.dpi,args.max_pages),ensure_ascii=False,indent=2))
