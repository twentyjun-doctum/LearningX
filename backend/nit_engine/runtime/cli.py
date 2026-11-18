"""Server-side integration CLI for *local* runs, not a web authentication system."""
import argparse,json,os
from .gateway import GeminiGateway,OpenAICompatibleGateway,DeterministicDemoGateway
from .coaching import run_coaching
from .history import QuestionHistory

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',required=True);p.add_argument('--question',required=True)
    p.add_argument('--student-id',required=True);p.add_argument('--course-id',required=True)
    p.add_argument('--provider',choices=('demo','openai','gemini'),default='demo')
    p.add_argument('--policy',default='RESTRICTED_HINT_ONLY');p.add_argument('--teacher-approved',action='store_true')
    p.add_argument('--history-db',default='out/nit_history.sqlite3')
    a=p.parse_args()
    gateway={'demo':DeterministicDemoGateway,'openai':OpenAICompatibleGateway,'gemini':GeminiGateway}[a.provider]()
    secret=os.environ.get('NIT_REVIEW_SIGNING_KEY','')
    db=QuestionHistory(a.history_db)
    initial=db.record(a.course_id,a.student_id,a.question,'initial')
    result=run_coaching(a.out,a.question,a.policy,a.teacher_approved,secret,a.student_id,gateway)
    result['question_id']=initial
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
