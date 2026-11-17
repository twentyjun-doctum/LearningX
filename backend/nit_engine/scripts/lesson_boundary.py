"""Evidence-graded lecture boundaries: no bare regex matches from body schedules."""
import re, unicodedata
from content_roles import get_text
WEEK=re.compile(r'(?<!\d)(?:제\s*)?([1-9]|1[0-6])\s*차\s*시|(?<!\d)([1-9]|1[0-6])\s*주\s*차|(?<![제\d])([1-9]|1[0-6])\s*강\s*[.:：]?')
FILE_WEEK=re.compile(r'^0?([1-9]|1[0-6])\s*(?:주\s*차|강)(?!\d)')


def detect(pages,filename=''):
    raw_candidates=[]
    fn=FILE_WEEK.search(unicodedata.normalize("NFKC",filename))
    if fn and pages:
        raw_candidates.append({'week':int(fn.group(1)),'start_page':1,'status':'filename_candidate_unverified',
                               'evidence_block_ids':[],'evidence':'filename_week_marker'})
    for p in pages:
        if p.get('content_role') in ('review_question','administrative_appendix','class_operation','class_activity_question','break','preview') or p.get('hidden_slide'):continue
        candidate=[(b,b.get('text','')) for b in p['blocks'][:6]
                   if b.get('type','text')=='text' and b.get('text')]
        if not candidate:continue
        # A genuine lecture-cover page is usually sparse and title-led.
        # Do not promote a week in a paragraph or repeated teaching notes.
        if len(p['blocks'])>3 and p.get('content_role')!='lecture_cover':continue
        prefix='\n'.join(t for _,t in candidate)[:250]
        match=WEEK.search(prefix)
        if not match:continue
        wk=int(match.group(1) or match.group(2) or match.group(3))
        before=prefix[:match.start()];after=prefix[match.end():match.end()+35]
        if re.search(r'(?:팀프로젝트|진행표|일정|완료|자기소개|마무리)',before[-50:]+after[:20]):continue
        if match.start()>38:continue
        if len(WEEK.findall(prefix))>1:continue
        # Reject references in parentheses, e.g. 'theory (3주차)'.
        if before.rstrip().endswith(('(', '（')):continue
        candidate_block=[b['id'] for b,t in candidate if match.group(0) in t][:2]
        raw_candidates.append({'week':wk,'start_page':p['page'],
                               'status':'detected_title_unverified','evidence_block_ids':candidate_block,
                               'evidence':'sparse_title_page_marker'})
    # Merge filename and actual title on page 1, prefer first true title.
    by_start={}
    for c in raw_candidates:
        k=c['start_page']
        if k not in by_start or c['status']=='detected_title_unverified':by_start[k]=c
    arr=[by_start[k] for k in sorted(by_start)]
    # Warn on non-monotonic numbers, but never delete a true physical title.
    for j,b in enumerate(arr):
        b['end_page']=arr[j+1]['start_page']-1 if j+1<len(arr) else len(pages)
        if j and b['week']<=arr[j-1]['week']:b['status']='needs_teacher_boundary_review'
    return arr
