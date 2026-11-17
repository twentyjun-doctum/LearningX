#!/usr/bin/env python3
import importlib.util, json, os, shutil
out={'pymupdf':importlib.util.find_spec('fitz') is not None,
     'pypdf_optional':importlib.util.find_spec('pypdf') is not None,
     'tesseract_binary_optional':bool(shutil.which('tesseract')),
     'rapidocr_optional':importlib.util.find_spec('rapidocr') is not None,
     'python_pptx':importlib.util.find_spec('pptx') is not None,
     'gemini_sdk_optional':importlib.util.find_spec('google') is not None,
     'gemini_api_key_present':bool(os.environ.get('GEMINI_API_KEY')),
     'libreoffice_for_pptx_vision':bool(shutil.which('libreoffice') or shutil.which('soffice'))}
print(json.dumps(out,indent=2))
out['pdf_mode']='pymupdf' if out['pymupdf'] else ('pypdf_limited' if out['pypdf_optional'] else 'unavailable')
print('PDF_MODE',out['pdf_mode'])
raise SystemExit(0 if (out['pymupdf'] or out['pypdf_optional']) and out['python_pptx'] else 2)
