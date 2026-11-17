"""Integration DEMO using a local test reviewer. Not actual faculty approval."""
import argparse,json,shutil,tempfile,sys
from pathlib import Path
from .gateway import ModelResult
from .coaching import run_coaching
from .history import QuestionHistory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import verified_coaching as vc
SECRET='DEMO_ONLY_NOT_REAL_FACULTY_APPROVAL__'*2

class DemoModel:
    def generate(self,system,user):
        supplied=json.loads(user.split('\n이전 응답')[0]); ids=[e['id'] for e in supplied['allowed_evidence']]
        return ModelResult(json.dumps({'schema_version':'nit_coaching_v1','question_level':'analyze',
            'evidence_ids':ids[:1],
            'follow_up_question':'서로 다른 커뮤니케이션 유형을 비교할 기준은 무엇일까요?'},ensure_ascii=False),
            'offline_demo','fixture_A')

def run(source_out, destination):
    target=Path(destination);target.mkdir(parents=True,exist_ok=True)
    shutil.copy2(Path(source_out)/'document.json',target/'document.json')
    doc,blocks=vc.load_blocks(target)
    eligible=[(p,b) for p in doc['pages'] for b in p['blocks'] if b.get('quality',{}).get('eligible_for_coaching')]
    selected=next(((p,b) for p,b in eligible if '커뮤니케이션' in (b.get('text') or '') and len(b.get('text') or '')>=25),None)
    if selected is None:raise RuntimeError('No suitable approved-candidate in source, demo not possible')
    p,b=selected
    approval=vc.approve(target,{'source_sha256':doc['source']['sha256'],'reviewer_id':'synthetic_faculty_fixture',
       'items':[{'page':p['page'],'block_id':b['id'],'decision':'approve',
                 'block_sha256':vc.block_digest(p['page'],b)}]},'synthetic_faculty_fixture',SECRET)
    query='커뮤니케이션의 개념은 어떤 사례에 적용할 수 있나요?'
    history=QuestionHistory(target/'history.sqlite3')
    question_id=history.record('demo_course','demo_student',query,'initial')
    result=run_coaching(target,query,'RESTRICTED_HINT_ONLY',True,SECRET,'demo_student',DemoModel())
    revision='커뮤니케이션의 유형을 비교할 때 피드백의 속도와 수신자 범위를 기준으로 볼 수 있을까요?'
    revision_id=history.record('demo_course','demo_student',revision,'revision',question_id)
    report={'source_sha256':doc['source']['sha256'],'note':'SYNTHETIC REVIEW AND MOCK LLM ONLY; not authorized for real students',
       'synthetic_approval_count':approval['approved'],'initial_id':question_id,'coaching':result,
       'revision_id':revision_id,'history':history.list_for_student('demo_course','demo_student')}
    (target/'demo_e2e_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--source-out',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args();print(json.dumps(run(a.source_out,a.out),ensure_ascii=False,indent=2))
