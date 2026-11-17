"""Offline contract stress check: intentionally variable model responses.
Not a measure of real provider quality or Korean educational effectiveness.
"""
import json
from pathlib import Path
from .coaching import run_coaching
from .gateway import ModelResult,GatewayFailure

class FlakyFixture:
    def __init__(self,which):self.which=which;self.calls=0
    def generate(self,system,user):
        self.calls+=1
        data=json.loads(user.split('\n이전 응답')[0]);id0=data['allowed_evidence'][0]['id']
        good={'schema_version':'nit_coaching_v1','question_level':'analyze',
              'evidence_ids':[id0], 'follow_up_question':'어떤 비교 기준을 적용하면 두 개념의 차이가 드러날까요?'}
        kind=self.which
        if kind=='valid':return ModelResult(json.dumps(good,ensure_ascii=False),'mock','model_a')
        if kind=='invalid_json':return ModelResult('invalid-response','mock','model_b')
        if kind=='invalid_evidence':good['evidence_ids']=['p999:fake']
        if kind=='answer_leak':good['follow_up_question']='정답은 3입니다. 이해했나요?'
        if kind=='transport_error':raise GatewayFailure('simulated outage')
        return ModelResult(json.dumps(good,ensure_ascii=False),'mock','model_c')

def run(out,secret):
    kinds=['valid','invalid_json','invalid_evidence','answer_leak','transport_error']*4
    results=[]
    for i,kind in enumerate(kinds):
        model=FlakyFixture(kind)
        response=run_coaching(out,'커뮤니케이션의 유형을 어떻게 비교해야 하나요?',
               'RESTRICTED_HINT_ONLY',True,secret,'student_fixture',model)
        results.append({'case_id':i+1,'fixture':kind,'mode':response['mode'],
                        'calls':model.calls,'source_text_exposed':'●대인' in json.dumps(response,ensure_ascii=False)})
    return {'simulated_requests':len(results), 'validated':sum(x['mode']=='MODEL_VALIDATED' for x in results),
            'safe_fallbacks':sum(x['mode']=='DETERMINISTIC_FALLBACK' for x in results),
            'raw_source_exposures':sum(x['source_text_exposed'] for x in results),'cases':results,
            'warning':'Mock data only. Not production-model behavior, semantic leakage guarantee, or real faculty approval.'}

if __name__=='__main__':
    from .demo_e2e import SECRET
    report=run('/mnt/data/nit-runtime-sprint1-real-demo',SECRET)
    path=Path('/mnt/data/nit-runtime-sprint1-mock20.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False))
