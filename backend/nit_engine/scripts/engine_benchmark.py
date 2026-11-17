#!/usr/bin/env python3
"""Per-page extraction benchmark. Optional engines run only when installed.
CER is calculated only against full page, reviewer-approved references.
No external network calls or model download attempts are made by default.
"""
import argparse,importlib.util,json,os,subprocess,tempfile,time
from review_auth import is_signed
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

def engine_available(engine):
    module={'pypdf':'pypdf','pymupdf':'fitz','tesseract':'fitz','docling':'docling','paddleocr':'paddleocr'}[engine]
    if not importlib.util.find_spec(module):return False,'missing_python_dependency'
    if engine=='tesseract' and not __import__('shutil').which('tesseract'):return False,'missing_tesseract_binary'
    return True,'available'

def extract(engine,source,page_no,dpi=150):
    if engine=='pypdf':
        from pypdf import PdfReader
        return PdfReader(str(source),strict=False).pages[page_no-1].extract_text() or ''
    import fitz
    with fitz.open(source) as doc:
        page=doc[page_no-1]
        if engine=='pymupdf':return page.get_text('text')
        if engine=='tesseract':
            tp=page.get_textpage_ocr(flags=3,language='kor+eng',dpi=dpi,full=True)
            return page.get_text('text',textpage=tp)
        # Heavy engines are opt-in only; tempfile PDF avoids processing entire 400-page file.
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)
            if engine=='docling':
                single=fitz.open();single.insert_pdf(doc,from_page=page_no-1,to_page=page_no-1)
                target=temp/'page.pdf';single.save(target);single.close()
                from docling.document_converter import DocumentConverter
                result=DocumentConverter().convert(str(target))
                return result.document.export_to_markdown()
            if engine=='paddleocr':
                png=temp/'page.png'
                png.write_bytes(page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False).tobytes('png'))
                from paddleocr import PPStructureV3
                pipeline=PPStructureV3(use_doc_orientation_classify=False,use_doc_unwarping=False)
                results=list(pipeline.predict(input=str(png)))
                # Persist raw results; versions differ; only extract string content when available.
                chunks=[]
                for res in results:
                    data=getattr(res,'json',None)
                    if callable(data):data=data()
                    chunks.append(json.dumps(data,ensure_ascii=False,default=str) if data else str(res))
                return '\n'.join(chunks)
    raise ValueError('Unsupported engine')

def compare(reference,hypothesis):
    # Native fallback: Levenshtein on full string (not local token recall).
    try:
        import jiwer
        return round(jiwer.cer(reference,hypothesis),5)
    except ImportError:
        from rapidfuzz.distance import Levenshtein
        return round(Levenshtein.distance(reference,hypothesis)/max(1,len(reference)),5)

def run(source,out,engines,pages=None,dpi=150):
    source=Path(source);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    from ingest import digest
    if doc['source']['sha256']!=digest(source):raise ValueError('Benchmark source SHA mismatch')
    ds=out/'evaluation_review_set.json';recs=json.loads(ds.read_text(encoding='utf-8'))['records'] if ds.exists() else []
    reference={r['page']:r['full_reference_text'] for r in recs if r.get('review_status')=='faculty_verified' and
              is_signed(r,doc['source']['sha256'],os.getenv('NIT_LABEL_SIGNING_KEY','')) and isinstance(r.get('full_reference_text'),str) and r['full_reference_text'].strip()}
    if pages is None: pages=list(sorted(reference))[:10] or [1]
    results=[]
    for engine in engines:
        ok,why=engine_available(engine)
        if not ok:
            results.append({'engine':engine,'status':'SKIPPED','reason':why});continue
        for p in pages:
            started=time.monotonic()
            try:
                txt=extract(engine,source,p,dpi)
                elapsed=round(time.monotonic()-started,3)
                hasref=p in reference
                results.append({'engine':engine,'page':p,'status':'OK','seconds':elapsed,'characters':len(txt),
                  'cer':compare(reference[p],txt) if hasref else None,
                  'reference_status':'verified_full_page' if hasref else 'none',
                  'warning':'PaddleOCR serialized layout output not directly comparable as text' if engine=='paddleocr' else None})
            except Exception as e:
                results.append({'engine':engine,'page':p,'status':'FAILED','error':str(e)[:300]})
    report={'schema_version':'nit_engine_benchmark_v1_5','source_sha256':doc['source']['sha256'],
      'engines':engines,'pages_requested':pages,'results':results,
      'note':'No CER without independently reviewed full-page transcript; skipped engine does not imply a comparison'}
    (out/'engine_benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('file');ap.add_argument('--out',required=True)
    ap.add_argument('--engines',default='pymupdf,pypdf,docling,paddleocr');ap.add_argument('--pages',default='')
    a=ap.parse_args();pages=[int(x) for x in a.pages.split(',') if x] if a.pages else None
    result=run(a.file,a.out,a.engines.split(','),pages)
    print(json.dumps({'pages':result['pages_requested'],'results':result['results']},ensure_ascii=False,indent=2))
