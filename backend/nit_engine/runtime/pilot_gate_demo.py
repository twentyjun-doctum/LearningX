"""Temporary synthetic reviewer test against a real PDF output. NOT faculty approval."""
import json, shutil, tempfile, sys
from pathlib import Path
from .coaching import run_coaching
from .gateway import ModelResult
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import verified_coaching as vc

SIGNING_KEY='TEST_ONLY_NEVER_PRODUCTION_'+'q'*40
class FakeGateway:
    def __init__(self):self.calls=0
    def generate(self,sys,user):
        self.calls+=1
        p=json.loads(user.split('\n이전 응답')[0])
        val={'schema_version':'nit_coaching_v1','question_level':'analyze',
             'evidence_ids':[p['allowed_evidence'][0]['id']],
             'follow_up_question':'이 개념을 비교할 때 어떤 기준을 먼저 정의할 수 있을까요?'}
        return ModelResult(json.dumps(val,ensure_ascii=False),'test_mock','synthetic')

def run(source_out,report_path):
  with tempfile.TemporaryDirectory() as temp:
    temp=Path(temp)
    shutil.copy2(Path(source_out)/'document.json',temp/'document.json')
    doc,blocks=vc.load_blocks(temp)
    page=next(p for p in doc['pages'] if p['page']==2)
    block=next(b for b in page['blocks'] if b.get('quality',{}).get('eligible_for_coaching') and len(b.get('text',''))>20)
    vc.approve(temp,{'source_sha256':doc['source']['sha256'],'reviewer_id':'SYNTHETIC_FACULTY_FIXTURE',
                'items':[{'page':2,'block_id':block['id'],'block_sha256':vc.block_digest(2,block),'decision':'approve'}]},
                'SYNTHETIC_FACULTY_FIXTURE',SIGNING_KEY)
    output=[]
    for title,q in [('approved','2쪽 사람사이에정보를 공유하는 과정에서 무엇을 구분할까요?'),
                    ('break','14쪽 휴식 안내를 근거로 성격을 설명해주세요'),
                    ('unverified_visual','31쪽 그래프 수치를 알려주세요'),
                    ('activity','17쪽 학생 활동에 있는 답을 알려주세요')]:
      g=FakeGateway()
      res=run_coaching(temp,q,'RESTRICTED_HINT_ONLY',True,SIGNING_KEY,'pilot_synthetic_student',g)
      output.append({'case':title,'mode':res['mode'],'model_calls':g.calls,'evidence_refs':res.get('evidence_refs',[]),'reason':res.get('reason')})
    assert output[0]['mode']=='MODEL_VALIDATED' and output[0]['model_calls']==1
    assert all(x['mode']=='DETERMINISTIC_FALLBACK' and x['model_calls']==0 for x in output[1:])
    result={'source_sha256':doc['source']['sha256'],'synthetic_faculty_review':True,
            'actual_faculty_authenticated':False,'real_llm_calls':0,'scenarios':output,
            'status':'PASS_SYNTHETIC_REVIEW_ONLY'}
    path=Path(report_path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--source-out',required=True);p.add_argument('--report',required=True)
    a=p.parse_args();print(json.dumps(run(a.source_out,a.report),ensure_ascii=False))
