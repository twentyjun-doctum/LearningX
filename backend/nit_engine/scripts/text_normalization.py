"""Shared Unicode and parser comparison utilities. No factual accuracy inference."""
import re, unicodedata
from collections import Counter

KNOWN_LIST_MARKERS={'\uf0fc','\uf0a7','\uf0d8','\uf0b7','\uf0a0','\uf0e0','\uf06c','\uf0b2'}
BAD_SCRIPTS=('ARABIC','HEBREW','CYRILLIC','DEVANAGARI','BENGALI','GURMUKHI','GUJARATI','ORIYA','ODIA','TAMIL','TELUGU','KANNADA','MALAYALAM','SINHALA','THAI','LAO')
def dangerous_characters(s):
    return [c for c in s if c in ('\x00','\ufffd') or
            (unicodedata.category(c) in ('Co','Cs') and c not in KNOWN_LIST_MARKERS) or
            (unicodedata.category(c)=='Cc' and c not in '\n\t\r\v')]
def normalize_bullets(s):
    return ''.join(('•' if c in KNOWN_LIST_MARKERS else c) for c in str(s or ''))
def clean_compare(s):
    s=unicodedata.normalize('NFKC',normalize_bullets(s))
    return re.sub(r'\s+',' ',s).strip()
def char_bag(s):
    return Counter(c.lower() for c in clean_compare(s) if c.isalnum())
def token_bag(s):
    return Counter(re.findall(r'[\w가-힣]{2,}',clean_compare(s).lower()))
def counter_f1(a,b):
    if not a and not b:return 1.0
    if not a or not b:return 0.0
    common=sum((a&b).values());return 2*common/max(sum(a.values())+sum(b.values()),1)
def numeric_bag(s):
    return Counter(re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)',clean_compare(s)))
